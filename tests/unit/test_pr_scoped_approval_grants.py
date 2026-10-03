"""Step 1F.2.7: an approval grant says *what* it covers, and stands on its own.

Two changes are under test and they are independent, so they are asserted
separately:

* **additive.** A grant authorises regardless of the holder's role. Before this
  step it only ever narrowed a role that already carried the permission, and a
  grant to an ``EMPLOYEE`` did nothing at all;
* **scoped.** A grant covers a set of content classifications and a set of
  channels, and nothing outside them. *Exactly* those: another channel, another
  classification, another gate, an item nobody classified and an item nobody has
  given a channel are all refused unless the grant says otherwise.

The suite walks the real services against a real (in-memory) database, and the
last section walks the source of every approval path to assert there is only one
implementation of the rule.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest

from meobot.application.pr_channel_service import CreateChannelCommand
from meobot.application.pr_services import build_pr_services
from meobot.db.models.pr import PrPlatform
from meobot.domain.identity.models import Role
from meobot.domain.permissions.matrix import Permission, has_permission
from meobot.domain.pr.errors import (
    PrConflictError,
    PrNotFoundError,
    PrPermissionDeniedError,
    PrValidationError,
)
from meobot.domain.pr.grants import ContentScopeKey, GrantScope
from meobot.domain.pr.models import (
    PrApprovalStage,
    PrChannelCategory,
    PrContentType,
    PrWorkflowStage,
)
from meobot.domain.pr.policy import GRANT_BACKED, PrCapability

# The world, the scope helper and the two clock constants now live in
# ``tests/unit/pr_world.py`` so Step 1F.2.8's bulk-approval suite argues against
# the identical fixture rather than a lookalike of it. The ``world`` fixture
# itself is registered in ``tests/unit/conftest.py`` and needs no import here.
from tests.unit.pr_world import ALL, NOW, SELECTED, TODAY, World, selected

pytestmark = pytest.mark.asyncio

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "meobot"


# =============================================================================
# 1. The scope arithmetic, with no database in the way
# =============================================================================


async def test_1a_a_blank_scope_covers_nothing() -> None:
    """Fail closed. A ``GrantScope()`` is what a bug produces, and it refuses."""
    scope = GrantScope()
    assert scope.is_empty
    assert not scope.covers(ContentScopeKey(PrContentType.FACEBOOK_POST, frozenset({uuid.uuid4()})))


async def test_1b_all_covers_the_missing_cases_too() -> None:
    """ "Every channel including none" is the only reading of *all* without a hole."""
    scope = GrantScope.everything()
    assert scope.covers(ContentScopeKey(None, frozenset()))
    assert scope.covers(ContentScopeKey(PrContentType.CORPORATE_TVC, frozenset({uuid.uuid4()})))


async def test_1c_a_channel_scope_is_a_subset_test_not_an_intersection() -> None:
    """An item on two channels is approved for both, so a grant must cover both.

    This is the whole "exact scope only" requirement in one assertion: covering
    CH-0001 does not license approving a piece that also goes to CH-0009,
    because that approval is what puts it on CH-0009.
    """
    one, two = uuid.uuid4(), uuid.uuid4()
    scope = selected(
        content_types=frozenset({PrContentType.FACEBOOK_POST}), channels=frozenset({one})
    )
    assert scope.covers(ContentScopeKey(PrContentType.FACEBOOK_POST, frozenset({one})))
    assert not scope.covers(ContentScopeKey(PrContentType.FACEBOOK_POST, frozenset({one, two})))


async def test_1d_absent_is_never_a_wildcard() -> None:
    """Unclassified and unassigned are denied unless the grant names them."""
    channel = uuid.uuid4()
    strict = selected(
        content_types=frozenset({PrContentType.FACEBOOK_POST}), channels=frozenset({channel})
    )
    assert not strict.covers(ContentScopeKey(None, frozenset({channel})))
    assert not strict.covers(ContentScopeKey(PrContentType.FACEBOOK_POST, frozenset()))

    permissive = selected(
        content_types=frozenset({PrContentType.FACEBOOK_POST}),
        channels=frozenset({channel}),
        unclassified=True,
        unassigned=True,
    )
    assert permissive.covers(ContentScopeKey(None, frozenset({channel})))
    assert permissive.covers(ContentScopeKey(PrContentType.FACEBOOK_POST, frozenset()))


# =============================================================================
# 2. Role permissions are the base, and they are untouched
# =============================================================================


async def test_2a_the_role_matrix_still_decides_everything_that_is_not_a_gate(
    world: World,
) -> None:
    """Only the three review gates are grant-backed. The other ten are role rules.

    Asserted through the service, so a change that quietly made a tenth
    capability grant-backed - or that let a grant decide one - fails here.
    ``PR_CONTENT_CREATE`` is ``script.submit``, which an ``EMPLOYEE`` holds;
    ``PR_CHANNEL_MANAGE`` is ``settings.write``, which they do not. Neither
    answer moves when a grant is handed out, because neither consults the table.
    """
    assert {
        PrCapability.PR_TEAM_LEAD_REVIEW,
        PrCapability.PR_HEAD_REVIEW,
        PrCapability.PR_INTERNAL_REVIEW,
    } == GRANT_BACKED
    member = world.actor(world.member)
    assert await world.capabilities.allows(member, PrCapability.PR_CONTENT_CREATE)
    assert not await world.capabilities.allows(member, PrCapability.PR_CHANNEL_MANAGE)

    await world.grant(world.member, PrCapability.PR_TEAM_LEAD_REVIEW, GrantScope.everything())
    assert await world.capabilities.allows(member, PrCapability.PR_CONTENT_CREATE)
    assert not await world.capabilities.allows(member, PrCapability.PR_CHANNEL_MANAGE)


async def test_2b_an_ordinary_member_without_a_grant_is_denied(world: World) -> None:
    content = await world.content(channels=(world.tiktok,))
    await world.to_team_lead_review(content.id)
    assert not await world.capabilities.can_approve(
        world.actor(world.member), content, PrApprovalStage.TEAM_LEAD_REVIEW
    )
    with pytest.raises(PrPermissionDeniedError):
        await world.approve(world.member, content.id, PrApprovalStage.TEAM_LEAD_REVIEW)


async def test_2c_a_team_lead_without_a_grant_is_denied_too(world: World) -> None:
    """The role carries ``script.review``, and that has never been enough.

    Step 1C.1's rule and Step 1F.2.7's agree here: the gates are separated by
    grants, not by roles, and nothing in this step widened a role.
    """
    assert has_permission(Role.TEAM_LEAD, Permission.SCRIPT_REVIEW)
    content = await world.content(channels=(world.tiktok,))
    await world.to_team_lead_review(content.id)
    assert not await world.capabilities.can_approve(
        world.actor(world.lead), content, PrApprovalStage.TEAM_LEAD_REVIEW
    )


async def test_2d_granting_promotes_nobody(world: World) -> None:
    """The member gains the gate they were given, and not one thing else."""
    await world.grant(
        world.member,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        selected(
            content_types=frozenset({PrContentType.FACEBOOK_POST}),
            channels=frozenset({world.facebook.id}),
        ),
    )
    assert world.member.role is Role.EMPLOYEE
    held = await world.capabilities.capabilities_for_actor(world.actor(world.member))
    assert PrCapability.PR_TEAM_LEAD_REVIEW in held
    assert PrCapability.PR_HEAD_REVIEW not in held
    assert PrCapability.PR_INTERNAL_REVIEW not in held
    assert PrCapability.PR_CHANNEL_MANAGE not in held


# =============================================================================
# 3. A matching grant admits, and an unmatching one does not
# =============================================================================


async def test_3a_a_member_with_a_matching_grant_may_approve(world: World) -> None:
    """The requirement, end to end: an EMPLOYEE records a real approval."""
    content = await world.content(
        content_type=PrContentType.FACEBOOK_POST, channels=(world.facebook,)
    )
    await world.to_team_lead_review(content.id)
    await world.grant(
        world.member,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        selected(
            content_types=frozenset({PrContentType.FACEBOOK_POST}),
            channels=frozenset({world.facebook.id}),
        ),
    )
    await world.approve(world.member, content.id, PrApprovalStage.TEAM_LEAD_REVIEW)
    await world.session.refresh(content)
    assert content.workflow_stage is PrWorkflowStage.HEAD_REVIEW


async def test_3b_the_right_gate_on_the_wrong_channel_is_refused(world: World) -> None:
    content = await world.content(
        content_type=PrContentType.FACEBOOK_POST, channels=(world.youtube,)
    )
    await world.to_team_lead_review(content.id)
    await world.grant(
        world.member,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        selected(
            content_types=frozenset({PrContentType.FACEBOOK_POST}),
            channels=frozenset({world.facebook.id}),
        ),
    )
    assert not await world.capabilities.can_approve(
        world.actor(world.member), content, PrApprovalStage.TEAM_LEAD_REVIEW
    )
    with pytest.raises(PrPermissionDeniedError) as raised:
        await world.approve(world.member, content.id, PrApprovalStage.TEAM_LEAD_REVIEW)
    assert raised.value.details["reason"] == "out_of_grant_scope"


async def test_3c_the_right_gate_on_the_wrong_classification_is_refused(world: World) -> None:
    content = await world.content(
        content_type=PrContentType.PRESS_ARTICLE, channels=(world.facebook,)
    )
    await world.to_team_lead_review(content.id)
    await world.grant(
        world.member,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        selected(
            content_types=frozenset({PrContentType.FACEBOOK_POST}),
            channels=frozenset({world.facebook.id}),
        ),
    )
    with pytest.raises(PrPermissionDeniedError) as raised:
        await world.approve(world.member, content.id, PrApprovalStage.TEAM_LEAD_REVIEW)
    assert raised.value.details["reason"] == "out_of_grant_scope"


async def test_3d_the_wrong_gate_is_refused(world: World) -> None:
    """A Team Lead grant is not a Head grant, however wide its scope."""
    await world.grant(world.member, PrCapability.PR_TEAM_LEAD_REVIEW, GrantScope.everything())
    content = await world.content(channels=(world.tiktok,))
    assert not await world.capabilities.can_approve(
        world.actor(world.member), content, PrApprovalStage.HEAD_REVIEW
    )
    assert not await world.capabilities.can_approve(
        world.actor(world.member), content, PrApprovalStage.INTERNAL_REVIEW
    )


# =============================================================================
# 4. Multi-select: one grant, several classifications, several channels
# =============================================================================


async def test_4a_one_grant_covers_several_channels(world: World) -> None:
    """The worked example from the requirement, both halves of the channel set."""
    await world.grant(
        world.member,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        selected(
            content_types=frozenset(
                {PrContentType.SHORT_VIDEO_SCRIPT, PrContentType.FACEBOOK_POST}
            ),
            channels=frozenset({world.tiktok.id, world.facebook.id}),
        ),
    )
    for channel in (world.tiktok, world.facebook):
        content = await world.content(
            content_type=PrContentType.SHORT_VIDEO_SCRIPT, channels=(channel,)
        )
        assert await world.capabilities.can_approve(
            world.actor(world.member), content, PrApprovalStage.TEAM_LEAD_REVIEW
        )
    outside = await world.content(
        content_type=PrContentType.SHORT_VIDEO_SCRIPT, channels=(world.youtube,)
    )
    assert not await world.capabilities.can_approve(
        world.actor(world.member), outside, PrApprovalStage.TEAM_LEAD_REVIEW
    )


async def test_4b_one_grant_covers_several_classifications(world: World) -> None:
    await world.grant(
        world.member,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        selected(
            content_types=frozenset(
                {PrContentType.SHORT_VIDEO_SCRIPT, PrContentType.FACEBOOK_POST}
            ),
            channels=frozenset({world.tiktok.id, world.facebook.id}),
        ),
    )
    for content_type in (PrContentType.SHORT_VIDEO_SCRIPT, PrContentType.FACEBOOK_POST):
        content = await world.content(content_type=content_type, channels=(world.tiktok,))
        assert await world.capabilities.can_approve(
            world.actor(world.member), content, PrApprovalStage.TEAM_LEAD_REVIEW
        )
    outside = await world.content(
        content_type=PrContentType.CORPORATE_TVC, channels=(world.tiktok,)
    )
    assert not await world.capabilities.can_approve(
        world.actor(world.member), outside, PrApprovalStage.TEAM_LEAD_REVIEW
    )


async def test_4c_an_item_on_two_channels_needs_both_of_them_in_scope(world: World) -> None:
    """Cross-posting is where an intersection rule would quietly widen a grant."""
    await world.grant(
        world.member,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        selected(
            content_types=frozenset({PrContentType.SHORT_VIDEO_SCRIPT}),
            channels=frozenset({world.tiktok.id}),
        ),
    )
    both = await world.content(
        content_type=PrContentType.SHORT_VIDEO_SCRIPT, channels=(world.tiktok, world.youtube)
    )
    assert not await world.capabilities.can_approve(
        world.actor(world.member), both, PrApprovalStage.TEAM_LEAD_REVIEW
    )


# =============================================================================
# 5. "All" scope
# =============================================================================


async def test_5a_all_channels_covers_a_channel_created_after_the_grant(world: World) -> None:
    """The reason ``ALL`` is a mode and not a snapshot of today's ids."""
    await world.grant(
        world.member,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        GrantScope(
            content_type_scope=SELECTED,
            content_types=frozenset({PrContentType.SHORT_VIDEO_SCRIPT}),
            channel_scope=ALL,
        ),
    )
    services = build_pr_services(world.session, world.settings)
    platform = PrPlatform(code="THREADS", name="Threads")
    world.session.add(platform)
    await world.session.flush()
    fresh = await services.channels.create_channel(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        command=CreateChannelCommand(
            name="Threads mới",
            platform_id=platform.id,
            brand_id=world.brand.id,
            category=PrChannelCategory.TEST,
        ),
    )
    content = await world.content(content_type=PrContentType.SHORT_VIDEO_SCRIPT, channels=(fresh,))
    assert await world.capabilities.can_approve(
        world.actor(world.member), content, PrApprovalStage.TEAM_LEAD_REVIEW
    )


async def test_5b_all_classifications_covers_unclassified_content(world: World) -> None:
    await world.grant(
        world.member,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        GrantScope(
            content_type_scope=ALL,
            channel_scope=SELECTED,
            channel_ids=frozenset({world.tiktok.id}),
        ),
    )
    content = await world.content(content_type=None, channels=(world.tiktok,))
    assert await world.capabilities.can_approve(
        world.actor(world.member), content, PrApprovalStage.TEAM_LEAD_REVIEW
    )
    # …and the classification axis being ALL does not open the channel axis.
    elsewhere = await world.content(content_type=None, channels=(world.youtube,))
    assert not await world.capabilities.can_approve(
        world.actor(world.member), elsewhere, PrApprovalStage.TEAM_LEAD_REVIEW
    )


# =============================================================================
# 6. The missing cases, which default to deny
# =============================================================================


async def test_6a_unclassified_content_is_denied_by_a_selected_scope(world: World) -> None:
    await world.grant(
        world.member,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        selected(
            content_types=frozenset({PrContentType.SHORT_VIDEO_SCRIPT}),
            channels=frozenset({world.tiktok.id}),
        ),
    )
    content = await world.content(content_type=None, channels=(world.tiktok,))
    assert not await world.capabilities.can_approve(
        world.actor(world.member), content, PrApprovalStage.TEAM_LEAD_REVIEW
    )


async def test_6b_content_with_no_channel_is_denied_by_a_selected_scope(world: World) -> None:
    await world.grant(
        world.member,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        selected(
            content_types=frozenset({PrContentType.SHORT_VIDEO_SCRIPT}),
            channels=frozenset({world.tiktok.id}),
        ),
    )
    content = await world.content(content_type=PrContentType.SHORT_VIDEO_SCRIPT, channels=())
    assert not await world.capabilities.can_approve(
        world.actor(world.member), content, PrApprovalStage.TEAM_LEAD_REVIEW
    )


async def test_6c_both_missing_cases_can_be_opted_into(world: World) -> None:
    """Denied by default, allowed only because somebody ticked the box."""
    await world.grant(
        world.member,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        selected(
            content_types=frozenset({PrContentType.SHORT_VIDEO_SCRIPT}),
            channels=frozenset({world.tiktok.id}),
            unclassified=True,
            unassigned=True,
        ),
    )
    unclassified = await world.content(content_type=None, channels=(world.tiktok,))
    unassigned = await world.content(content_type=PrContentType.SHORT_VIDEO_SCRIPT, channels=())
    for content in (unclassified, unassigned):
        assert await world.capabilities.can_approve(
            world.actor(world.member), content, PrApprovalStage.TEAM_LEAD_REVIEW
        )


# =============================================================================
# 7. Time and revocation
# =============================================================================


async def test_7a_an_expired_grant_is_ignored(world: World) -> None:
    yesterday = (NOW - timedelta(days=1)).date()
    await world.grant(
        world.member,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        GrantScope.everything(),
        effective_to=yesterday,
    )
    content = await world.content(channels=(world.tiktok,))
    assert not await world.capabilities.can_approve(
        world.actor(world.member), content, PrApprovalStage.TEAM_LEAD_REVIEW, on=TODAY
    )
    # And it worked on its last day, which is what makes the bound closed.
    assert await world.capabilities.can_approve(
        world.actor(world.member), content, PrApprovalStage.TEAM_LEAD_REVIEW, on=yesterday
    )


async def test_7b_a_revoked_grant_stops_working_at_once(world: World) -> None:
    """No cache, no TTL, and no waiting for the day to end."""
    grant_id = await world.grant(
        world.member, PrCapability.PR_TEAM_LEAD_REVIEW, GrantScope.everything()
    )
    content = await world.content(
        content_type=PrContentType.SHORT_VIDEO_SCRIPT, channels=(world.tiktok,)
    )
    await world.to_team_lead_review(content.id)
    assert await world.capabilities.can_approve(
        world.actor(world.member), content, PrApprovalStage.TEAM_LEAD_REVIEW
    )

    await world.capabilities.revoke(
        actor=world.actor(world.owner), request_id=world.request_id, grant_id=grant_id
    )
    assert not await world.capabilities.can_approve(
        world.actor(world.member), content, PrApprovalStage.TEAM_LEAD_REVIEW
    )
    with pytest.raises(PrPermissionDeniedError):
        await world.approve(world.member, content.id, PrApprovalStage.TEAM_LEAD_REVIEW)


async def test_7c_a_revocation_is_not_recoverable_by_asking_about_an_earlier_day(
    world: World,
) -> None:
    grant_id = await world.grant(
        world.member, PrCapability.PR_TEAM_LEAD_REVIEW, GrantScope.everything()
    )
    await world.capabilities.revoke(
        actor=world.actor(world.owner), request_id=world.request_id, grant_id=grant_id
    )
    content = await world.content(channels=(world.tiktok,))
    for day in (date(2026, 1, 1), TODAY, date(2027, 1, 1)):
        assert not await world.capabilities.can_approve(
            world.actor(world.member), content, PrApprovalStage.TEAM_LEAD_REVIEW, on=day
        )


# =============================================================================
# 8. Only a grant manager grants, and nobody edits their own scope
# =============================================================================


async def test_8a_only_a_grant_manager_may_grant_or_revoke(world: World) -> None:
    """``user.role.manage`` - OWNER-only, and no capability guards itself."""
    for actor in (world.actor(world.lead), world.actor(world.member)):
        with pytest.raises(PrPermissionDeniedError):
            await world.capabilities.grant(
                actor=actor,
                request_id=world.request_id,
                user_id=world.member.id,
                capability=PrCapability.PR_TEAM_LEAD_REVIEW,
                scope=GrantScope.everything(),
            )


async def test_8b_a_holder_cannot_widen_their_own_grant(world: World) -> None:
    """Holding the widest possible review grant confers no power to grant."""
    grant_id = await world.grant(
        world.member, PrCapability.PR_TEAM_LEAD_REVIEW, GrantScope.everything()
    )
    with pytest.raises(PrPermissionDeniedError):
        await world.capabilities.grant(
            actor=world.actor(world.member),
            request_id=world.request_id,
            user_id=world.member.id,
            capability=PrCapability.PR_HEAD_REVIEW,
            scope=GrantScope.everything(),
        )
    with pytest.raises(PrPermissionDeniedError):
        await world.capabilities.revoke(
            actor=world.actor(world.member),
            request_id=world.request_id,
            grant_id=grant_id,
        )


async def test_8c_a_scope_that_covers_nothing_is_refused(world: World) -> None:
    with pytest.raises(PrValidationError):
        await world.grant(world.member, PrCapability.PR_TEAM_LEAD_REVIEW, GrantScope())


async def test_8d_a_scope_naming_a_channel_that_does_not_exist_is_refused(
    world: World,
) -> None:
    """Otherwise the grant silently does nothing and nobody can tell."""
    with pytest.raises(PrNotFoundError):
        await world.grant(
            world.member,
            PrCapability.PR_TEAM_LEAD_REVIEW,
            selected(
                content_types=frozenset({PrContentType.FACEBOOK_POST}),
                channels=frozenset({uuid.uuid4()}),
            ),
        )


async def test_8e_two_different_scopes_coexist_and_an_identical_one_does_not(
    world: World,
) -> None:
    """The dropped unique index, and what replaced it."""
    first = selected(
        content_types=frozenset({PrContentType.FACEBOOK_POST}),
        channels=frozenset({world.facebook.id}),
    )
    second = selected(
        content_types=frozenset({PrContentType.SHORT_VIDEO_SCRIPT}),
        channels=frozenset({world.tiktok.id}),
    )
    await world.grant(world.member, PrCapability.PR_TEAM_LEAD_REVIEW, first)
    await world.grant(world.member, PrCapability.PR_TEAM_LEAD_REVIEW, second)
    with pytest.raises(PrConflictError):
        await world.grant(world.member, PrCapability.PR_TEAM_LEAD_REVIEW, first)

    # Both are in force, and neither leaks into the other's scope.
    facebook_post = await world.content(
        content_type=PrContentType.FACEBOOK_POST, channels=(world.facebook,)
    )
    tiktok_script = await world.content(
        content_type=PrContentType.SHORT_VIDEO_SCRIPT, channels=(world.tiktok,)
    )
    crossed = await world.content(
        content_type=PrContentType.FACEBOOK_POST, channels=(world.tiktok,)
    )
    for content in (facebook_post, tiktok_script):
        assert await world.capabilities.can_approve(
            world.actor(world.member), content, PrApprovalStage.TEAM_LEAD_REVIEW
        )
    assert not await world.capabilities.can_approve(
        world.actor(world.member), crossed, PrApprovalStage.TEAM_LEAD_REVIEW
    )


async def test_8f_revoking_one_of_two_grants_leaves_the_other(world: World) -> None:
    keep = selected(
        content_types=frozenset({PrContentType.SHORT_VIDEO_SCRIPT}),
        channels=frozenset({world.tiktok.id}),
    )
    drop = selected(
        content_types=frozenset({PrContentType.FACEBOOK_POST}),
        channels=frozenset({world.facebook.id}),
    )
    await world.grant(world.member, PrCapability.PR_TEAM_LEAD_REVIEW, keep)
    drop_id = await world.grant(world.member, PrCapability.PR_TEAM_LEAD_REVIEW, drop)

    # Naming the person and the gate is now ambiguous, and it says so rather
    # than taking away whichever it found first.
    with pytest.raises(PrValidationError):
        await world.capabilities.revoke(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            user_id=world.member.id,
            capability=PrCapability.PR_TEAM_LEAD_REVIEW,
        )
    await world.capabilities.revoke(
        actor=world.actor(world.owner), request_id=world.request_id, grant_id=drop_id
    )
    kept = await world.content(
        content_type=PrContentType.SHORT_VIDEO_SCRIPT, channels=(world.tiktok,)
    )
    dropped = await world.content(
        content_type=PrContentType.FACEBOOK_POST, channels=(world.facebook,)
    )
    assert await world.capabilities.can_approve(
        world.actor(world.member), kept, PrApprovalStage.TEAM_LEAD_REVIEW
    )
    assert not await world.capabilities.can_approve(
        world.actor(world.member), dropped, PrApprovalStage.TEAM_LEAD_REVIEW
    )


# =============================================================================
# 9. The HTTP surface asks the same question
# =============================================================================


async def test_9a_a_direct_api_approval_outside_the_scope_is_refused(world: World) -> None:
    """No client-side gate anywhere: the route is hit directly and still 403s."""
    await world.grant(
        world.member,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        selected(
            content_types=frozenset({PrContentType.FACEBOOK_POST}),
            channels=frozenset({world.facebook.id}),
        ),
    )
    content_id = await world.content_id(
        content_type=PrContentType.SHORT_VIDEO_SCRIPT, channels=(world.youtube,)
    )
    await world.to_team_lead_review(content_id)
    world.act_as(world.member)
    response = world.client.post(
        f"/api/pr/contents/{content_id}/reviews",
        json={"decision": "APPROVED", "version_reviewed": 1},
    )
    assert response.status_code == 403, response.text


async def test_9b_the_same_call_inside_the_scope_succeeds(world: World) -> None:
    await world.grant(
        world.member,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        selected(
            content_types=frozenset({PrContentType.FACEBOOK_POST}),
            channels=frozenset({world.facebook.id}),
        ),
    )
    content_id = await world.content_id(
        content_type=PrContentType.FACEBOOK_POST, channels=(world.facebook,)
    )
    await world.to_team_lead_review(content_id)
    world.act_as(world.member)
    response = world.client.post(
        f"/api/pr/contents/{content_id}/reviews",
        json={"decision": "APPROVED", "version_reviewed": 1},
    )
    assert response.status_code == 201, response.text
    assert response.json()["content"]["workflow_stage"] == PrWorkflowStage.HEAD_REVIEW.value


async def test_9c_available_actions_offers_no_approval_outside_the_scope(
    world: World,
) -> None:
    """The panel draws what the server lists, so the offer cannot outrun the write."""
    await world.grant(
        world.member,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        selected(
            content_types=frozenset({PrContentType.FACEBOOK_POST}),
            channels=frozenset({world.facebook.id}),
        ),
    )
    outside = await world.content_id(
        content_type=PrContentType.SHORT_VIDEO_SCRIPT, channels=(world.youtube,)
    )
    inside = await world.content_id(
        content_type=PrContentType.FACEBOOK_POST, channels=(world.facebook,)
    )
    await world.to_team_lead_review(outside)
    await world.to_team_lead_review(inside)
    world.act_as(world.member)

    def kinds(content_id: uuid.UUID) -> set[str]:
        response = world.client.get(f"/api/pr/contents/{content_id}/available-actions")
        assert response.status_code == 200, response.text
        return {action["action"] for action in response.json()["available_actions"]}

    assert "APPROVAL" not in kinds(outside)
    assert "APPROVAL" in kinds(inside)


async def test_9d_the_grant_list_carries_the_whole_scope(world: World) -> None:
    await world.grant(
        world.member,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        selected(
            content_types=frozenset(
                {PrContentType.SHORT_VIDEO_SCRIPT, PrContentType.FACEBOOK_POST}
            ),
            channels=frozenset({world.tiktok.id, world.facebook.id}),
        ),
    )
    world.act_as(world.owner)
    response = world.client.get("/api/pr/capabilities")
    assert response.status_code == 200, response.text
    rows = response.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["capability"] == "PR_TEAM_LEAD_REVIEW"
    assert row["scope"]["content_type_scope"] == "SELECTED"
    assert sorted(row["scope"]["content_types"]) == ["FACEBOOK_POST", "SHORT_VIDEO_SCRIPT"]
    assert sorted(row["scope"]["channel_ids"]) == sorted(
        [str(world.tiktok.id), str(world.facebook.id)]
    )
    assert row["scope"]["include_unclassified_content"] is False
    assert row["scope"]["include_unassigned_channel"] is False
    assert row["requires_role_baseline"] is False
    assert "note" not in row


async def test_9e_granting_and_revoking_over_http(world: World) -> None:
    world.act_as(world.owner)
    created = world.client.post(
        "/api/pr/capabilities/grant",
        json={
            "user_id": str(world.member.id),
            "capability": "PR_TEAM_LEAD_REVIEW",
            "scope": {
                "content_type_scope": "SELECTED",
                "content_types": ["FACEBOOK_POST"],
                "channel_scope": "ALL",
            },
            "note": "Duyệt bài Facebook",
        },
    )
    assert created.status_code == 201, created.text
    grant_id = created.json()["id"]
    assert created.json()["scope"]["channel_scope"] == "ALL"

    revoked = world.client.post("/api/pr/capabilities/revoke", json={"grant_id": grant_id})
    assert revoked.status_code == 200, revoked.text
    assert world.client.get("/api/pr/capabilities").json() == []


async def test_9f_a_non_manager_cannot_grant_over_http(world: World) -> None:
    world.act_as(world.member)
    response = world.client.post(
        "/api/pr/capabilities/grant",
        json={
            "user_id": str(world.member.id),
            "capability": "PR_TEAM_LEAD_REVIEW",
            "scope": {"content_type_scope": "ALL", "channel_scope": "ALL"},
        },
    )
    assert response.status_code == 403, response.text


# =============================================================================
# 10. One rule, one place
# =============================================================================


async def test_10a_nothing_outside_the_domain_matches_a_scope_itself() -> None:
    """Scope arithmetic lives in one module and one service, and nowhere else.

    The sweep looks for the two attributes a re-implementation would have to
    read - ``content_type_scope`` and ``channel_scope`` - outside the files
    entitled to them. A second copy of the rule is how a screen and a write come
    to disagree about who may approve.
    """
    entitled = {
        SRC / "domain" / "pr" / "grants.py",
        # Step 1F.2.7a. The SQL twin, and the *only* other expression of the
        # rule. It is entitled because a read model cannot filter in Python
        # without breaking pagination - and it is safe because
        # ``test_10d`` proves the two agree on every cell of a matrix.
        SRC / "application" / "pr_grant_scope_sql.py",
        SRC / "application" / "pr_capability_service.py",
        SRC / "db" / "models" / "pr_authorization.py",
        SRC / "api" / "schemas" / "pr.py",
        SRC / "api" / "routers" / "pr.py",
    }
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        if path in entitled:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if line.lstrip().startswith(("#", "*", '"', "'", ":")):
                continue
            if "content_type_scope" in line or "channel_scope" in line:
                offenders.append(f"{path.relative_to(ROOT)}:{number}")
    assert offenders == [], offenders


async def test_10b_every_approval_path_passes_the_content_item() -> None:
    """The three places an approval right is decided all hand over the item.

    Asserted by shape rather than by behaviour because the failure it guards
    against is a *new* call site added without the argument: a scope check that
    silently degrades to "could you ever approve anything" is invisible until
    somebody approves the wrong thing.
    """
    approval = (SRC / "application" / "pr_approval_service.py").read_text(encoding="utf-8")
    assert "require_approval(actor, content, command.approval_stage)" in approval

    actions = (SRC / "application" / "pr_action_service.py").read_text(encoding="utf-8")
    assert "can_approve(actor, content, gate, on=on)" in actions

    undo = (SRC / "application" / "pr_undo_service.py").read_text(encoding="utf-8")
    assert "can_approve(actor, content, stage)" in undo


async def test_10c_the_service_never_caches_an_effective_grant() -> None:
    """Requirement 10: a revocation must not wait for a cache to expire."""
    source = (SRC / "application" / "pr_capability_service.py").read_text(encoding="utf-8")
    for forbidden in ("lru_cache", "cached_property", "TTLCache", "self._cache"):
        assert forbidden not in source, forbidden
