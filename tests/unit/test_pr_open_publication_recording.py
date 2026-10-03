"""Step 1F.2.3f.3 - anybody who may see a piece may write down that it went out.

Numbered 1-50, following the requirement numbering the step was specified with:
1-10 create authorization, 11-15 channel integrity, 16-21 output integrity,
22-27 workflow, 28-34 ownership, 35-40 reversal, 41-45 attribution, 46-50 the
security boundary.

The complaint behind it
------------------------

Step 1F.2.3f.2 split ``publish.social`` in two so that the person who had just
posted the video could record it, and then narrowed the new half with a
**channel** rule: an in-force ``pr_channel_assignments`` row, or a planned target
of a piece the actor was responsible for. A normal member with neither got

    Bạn chưa được phép ghi nhận bài đăng trên kênh này.

which is the wrong answer to the ordinary case. The person who posts a cut is
routinely neither the channel's assignee nor the content's owner, and the two
ways round the refusal - an assignment nobody meant, a back-dated plan - both
write a worse fact into the database than the publication being worked around.

So creating a publication is now the module's **view** rule, the same one Step
1F.2.3g gave derivatives and comments, asked of the same
``PrContentService.require_viewable_content`` rather than copied.

What this file is really testing
---------------------------------

Almost every test below is one of two claims:

* **creation widened** - five different relationships to a piece, and not one of
  them is consulted any more;
* **nothing else did** - the channel, the output, the stage, the URL, editing
  somebody else's row and reversing anything are all exactly where Step
  1F.2.3f/f.1/f.2 left them, and each is asserted separately because "we opened
  one door" is only true if the others are still shut.

The world and the walk to ``READY_TO_PUBLISH`` are the siblings', imported
rather than rebuilt, for the reason those files already give: this step's whole
subject is who may act on a piece that was really produced.
"""

from __future__ import annotations

# The ``world`` fixture is a module-level name and every test takes a parameter
# of the same name; ruff reads that as a redefinition on every signature.
# ruff: noqa: F811
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from meobot.application.pr_channel_service import UpdateChannelCommand
from meobot.application.pr_publication_service import (
    RegisterPublicationCommand,
    UpdatePublicationCommand,
)
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.pr_reporting import PrPublication
from meobot.domain.identity.models import Role
from meobot.domain.pr.errors import (
    PrConflictError,
    PrNotFoundError,
    PrPermissionDeniedError,
    PrPublishedContentError,
    PrValidationError,
    PrWorkflowTransitionError,
)
from meobot.domain.pr.models import (
    PrChannelAssignmentRole,
    PrChannelStatus,
    PrWorkflowStage,
)
from meobot.domain.pr.policy import PrCapability
from meobot.domain.pr.reporting import PrPublicationStatus
from meobot.domain.pr.workflow import PUBLISHABLE_STAGES
from tests.unit.test_pr_asset_locations_and_contributors import unplanned_channel
from tests.unit.test_pr_derivatives_and_publications import (
    POST_URL,
    add_derivative,
    force_stage,
    publish,
    ready_to_publish,
    stage_of,
)
from tests.unit.test_pr_open_contributions_and_comments import outsider
from tests.unit.test_pr_production_lifecycle import (  # noqa: F401 - `world` is a fixture
    NOW,
    World,
    world,
)
from tests.unit.test_pr_publication_corrections import _instant

pytestmark = pytest.mark.asyncio


# --- Helpers ----------------------------------------------------------------


async def publications_of(world: World, content_id: uuid.UUID) -> list[PrPublication]:
    rows = await world.session.execute(
        select(PrPublication)
        .where(PrPublication.content_id == content_id)
        .order_by(PrPublication.code.asc())
    )
    return list(rows.scalars().all())


async def post_publication(world: World, content_id: uuid.UUID, **body: object):
    """The HTTP path, which is the one that actually has to be safe."""
    return world.client.post(f"/api/pr/contents/{content_id}/publications", json=body)


async def actions_for(world: World, content_id: uuid.UUID, *, actor) -> set[str]:  # type: ignore[no-untyped-def]
    content = await world.reload(content_id)
    offered = await world.services.actions.for_content(actor=world.actor(actor), content=content)
    return {action.kind.value for action in offered}


# ===========================================================================
# 1-10: WHO MAY RECORD ONE
# ===========================================================================


@pytest.mark.parametrize("who", ["owner", "member", "lead", "head", "other"])
async def test_01_05_every_relationship_to_the_piece_may_record_one(world: World, who: str) -> None:
    """Requirements 1, 2, 3, 4 and 5, in one table because they are one rule.

    An unrelated authenticated viewer, the content's owner, the person
    responsible for it, its producer and a reviewer are five different
    relationships to the piece, and since this step **not one of them is
    consulted**. Parametrised rather than written five times precisely to make
    that visible: if any relationship ever started mattering again, four of these
    would still pass and one would not.

    ``world.member`` is both owner and producer here - ``ready_to_publish`` walks
    them through it - and ``world.other`` is the plain colleague who owns
    nothing, is assigned to nothing and holds no grant. The channel is one nobody
    in this test operates and nobody planned for, which is exactly the
    combination the old rule refused.
    """
    content_id, master = await ready_to_publish(world)
    channel = await unplanned_channel(world, f"Kênh {who}")
    actor = getattr(world, who)

    recorded = await publish(
        world, content_id, channel_id=channel.id, submission=master, actor=actor
    )
    assert recorded.publisher_user_id == actor.id
    assert recorded.channel_id == channel.id


async def test_06_a_viewer_without_the_publication_capability_still_records_one(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requirement 6. The old capability is not consulted, and this proves it.

    Both publication capabilities are switched off for the duration of the call.
    ``PR_PUBLICATION_REGISTER`` was never held by an ``EMPLOYEE`` anyway;
    ``PR_PUBLICATION_CREATE`` is the one Step 1F.2.3f.2 introduced and this step
    stops asking for. Neither is in the create path any more, so removing both
    changes nothing about recording - and the assertion would fail the moment
    somebody wired one back in "just to be safe".
    """
    content_id, master = await ready_to_publish(world)

    async def denies(self, actor, capability, *, on=None):  # type: ignore[no-untyped-def]
        del self, actor, capability, on
        return False

    monkeypatch.setattr(
        "meobot.application.pr_capability_service.PrCapabilityService.allows", denies
    )
    recorded = await publish(world, content_id, submission=master, actor=world.other)
    assert recorded.publisher_user_id == world.other.id


async def test_07_a_viewer_with_no_channel_assignment_still_records_one(
    world: World,
) -> None:
    """Requirement 7. The exact refusal this step exists to delete.

    ``world.other`` operates no channel, and the channel is not a planned target
    of this content. Under Step 1F.2.3f.2 both branches of ``_operates`` failed
    and the answer was *"Bạn chưa được phép ghi nhận bài đăng trên kênh này."*
    with ``reason='channel_not_yours'``. There is no such reason any more,
    because there is no such rule - and no ``pr_channel_assignments`` row was
    invented to make this pass.
    """
    content_id, master = await ready_to_publish(world)
    stranger = await outsider(world, "Lê Người Đăng")
    channel = await unplanned_channel(world, "Kênh chưa ai được phân công")

    recorded = await publish(
        world, content_id, channel_id=channel.id, submission=master, actor=stranger
    )
    assert recorded.publisher_user_id == stranger.id

    assigned = await world.services.channels.list_channel_assignments(
        actor=world.actor(world.owner), channel_id=channel.id
    )
    assert list(assigned) == []


async def test_08_somebody_who_cannot_view_the_content_cannot_record_one(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requirement 8. The rule is the *view* rule, and this is what proves it.

    Every MeoBot role carries ``script.read``, so there is no role that cannot
    see PR content and no honest way to build such an actor. What there is, is
    the gate itself - so the permission is removed for the duration of one call
    and both the read and the write are asked.

    **Both refuse, for the same reason.** That is the claim: recording a
    publication is not a second, laxer authorization path that happens to be open
    - it is the same check ``PrQueryService`` makes before it will show the piece
    at all. The same shape as Step 1F.2.3g's requirement 6, deliberately.
    """
    content_id, master = await ready_to_publish(world)
    monkeypatch.setattr("meobot.domain.pr.policy.has_permission", lambda role, permission: False)

    with pytest.raises(PrPermissionDeniedError):
        await world.services.queries.get_content(
            actor=world.actor(world.other), content_id=content_id
        )
    with pytest.raises(PrPermissionDeniedError):
        await publish(world, content_id, submission=master, actor=world.other)
    assert await publications_of(world, content_id) == []


async def test_09_the_rule_reads_no_role_string_and_no_relationship(world: World) -> None:
    """Requirement 9. Asked of the predicate itself rather than through a walk.

    ``may_record_publication`` is what the action list and the write both call,
    and it must answer the same for five people whose roles, ownership,
    responsibility and production relationships to this piece are all different.
    Anything that shortcut on ``Role`` or on ``owner_user_id`` would show up here
    as a disagreement.
    """
    content_id, _ = await ready_to_publish(world)
    content = await world.reload(content_id)
    stranger = await outsider(world, "Phạm Vô Can")

    for person in (world.owner, world.lead, world.head, world.member, world.other, stranger):
        assert await world.services.publications.may_record_publication(
            world.actor(person), content
        )


async def test_10_recording_grants_an_ordinary_member_nothing_else(world: World) -> None:
    """Requirement 10, and requirement 25 of the specification: no broad grant.

    The fix is a **narrow create rule**, not a wider permission. So the member
    who just recorded a publication is checked for the three capabilities that
    would have been the lazy way to make the button appear - and holds none of
    them, on this content or any other.

    The action list is checked too, because that is where a capability leak would
    become visible to a user: ``RECORD_PUBLICATION`` is offered and the two
    administrative publication actions are not.
    """
    content_id, master = await ready_to_publish(world)
    await publish(world, content_id, submission=master, actor=world.other)
    actor = world.actor(world.other)

    for capability in (
        PrCapability.PR_PUBLICATION_REGISTER,
        PrCapability.PR_CHANNEL_MANAGE,
        PrCapability.PR_CONTENT_CANCEL,
    ):
        assert not await world.services.capabilities.allows(actor, capability), capability

    offered = await actions_for(world, content_id, actor=world.other)
    assert "RECORD_PUBLICATION" in offered
    assert "EDIT_ANY_PUBLICATION" not in offered
    assert "REVERSE_PUBLICATION" not in offered


# ===========================================================================
# 11-15: THE CHANNEL IS STILL CHECKED - AS DATA, NOT AS PERMISSION
# ===========================================================================


async def test_11_a_real_channel_in_scope_succeeds(world: World) -> None:
    """Requirement 11. The planned target, recorded by somebody unrelated."""
    content_id, master = await ready_to_publish(world)
    recorded = await publish(world, content_id, submission=master, actor=world.other)
    assert recorded.channel_id == world.channel_id


async def test_12_13_a_channel_that_does_not_exist_is_refused(world: World) -> None:
    """Requirements 12 and 13. Integrity survived the widening of authorization.

    MeoBot's channels are not tenant-scoped - ``pr_channels`` carries a nullable
    ``brand_id`` and no company column - so "a channel belonging to another
    tenant" is, in this schema, exactly "a channel id that is not in the table".
    Requirement 13 is therefore this test rather than a separate one, and the
    forged id is checked over HTTP too: a client that invents a UUID gets a
    named refusal and not a foreign-key traceback.
    """
    content_id, master = await ready_to_publish(world)
    forged = uuid.uuid4()

    with pytest.raises(PrNotFoundError) as raised:
        await publish(world, content_id, channel_id=forged, submission=master, actor=world.other)
    assert raised.value.details["field"] == "channel_id"
    assert await publications_of(world, content_id) == []

    world.act_as(world.other)
    response = await post_publication(
        world,
        content_id,
        channel_id=str(forged),
        published_at=NOW.isoformat(),
        production_submission_id=str(master.id),
        url=POST_URL,
    )
    assert response.status_code == 404, response.text


async def test_14_a_retired_channel_still_takes_a_back_filled_posting(world: World) -> None:
    """Requirement 14. The canonical rule, unchanged: existence, not status.

    ``_require_channel`` has never checked a channel's status, and Step 1F.2.3f
    says why - a publication records something that already happened, and a
    channel the team has since retired is exactly the kind a back-filled posting
    belongs to. Widening *who* may record must not quietly narrow *what* they
    may record against.
    """
    content_id, master = await ready_to_publish(world)
    channel = await unplanned_channel(world, "Kênh đã đóng")
    await world.services.channels.update_channel(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        command=UpdateChannelCommand(channel_id=channel.id, status=PrChannelStatus.ARCHIVED),
    )

    recorded = await publish(
        world, content_id, channel_id=channel.id, submission=master, actor=world.other
    )
    assert recorded.channel_id == channel.id


async def test_15_channel_permission_is_not_consulted_at_all(world: World) -> None:
    """Requirement 15. Channel administration is a different question, still asked.

    Two claims in one test, and they are the whole shape of the step:

    * ``world.other`` may **record** a posting on this channel without holding
      ``PR_CHANNEL_MANAGE``;
    * and still may not **manage** the channel, which is what that capability is
      for. Opening publication creation did not open channel configuration.
    """
    content_id, master = await ready_to_publish(world)
    channel = await unplanned_channel(world, "Kênh của bộ phận khác")
    actor = world.actor(world.other)
    assert not await world.services.capabilities.allows(actor, PrCapability.PR_CHANNEL_MANAGE)

    await publish(world, content_id, channel_id=channel.id, submission=master, actor=world.other)

    with pytest.raises(PrPermissionDeniedError):
        await world.services.channels.assign_user(
            actor=actor,
            request_id=world.request_id,
            channel_id=channel.id,
            user_id=world.other.id,
            assignment_role=PrChannelAssignmentRole.CHANNEL_OWNER,
            effective_from=NOW.date(),
        )


# ===========================================================================
# 16-21: WHICH FILE WENT OUT - STEP 1F.2.3f'S LINEAGE, UNCHANGED
# ===========================================================================


async def test_16_an_original_submission_from_this_content_succeeds(world: World) -> None:
    """Requirement 16."""
    content_id, master = await ready_to_publish(world)
    recorded = await publish(world, content_id, submission=master, actor=world.other)
    assert recorded.production_submission_id == master.id
    assert recorded.derivative_id is None


async def test_17_a_derivative_from_this_content_succeeds(world: World) -> None:
    """Requirement 17. The other half of the XOR, recorded by a plain viewer."""
    content_id, _ = await ready_to_publish(world)
    cut = await add_derivative(world, content_id, actor=world.other)
    recorded = await publish(world, content_id, derivative=cut, actor=world.other)
    assert recorded.derivative_id == cut.id
    assert recorded.production_submission_id is None


async def test_18_an_output_belonging_to_another_content_is_refused(world: World) -> None:
    """Requirement 18. Widening permissions did not loosen the record."""
    _first_id, first_master = await ready_to_publish(world)
    second_id, _ = await ready_to_publish(world)

    with pytest.raises(PrValidationError) as raised:
        await publish(world, second_id, submission=first_master, actor=world.other)
    assert raised.value.details["reason"] == "foreign_output"

    world.act_as(world.other)
    response = await post_publication(
        world,
        second_id,
        channel_id=str(world.channel_id),
        published_at=NOW.isoformat(),
        production_submission_id=str(first_master.id),
        url=POST_URL,
    )
    assert response.status_code == 422, response.text


async def test_19_21_exactly_one_output_reference_is_required(world: World) -> None:
    """Requirements 19, 20 and 21. Never both, never neither.

    Only half of it is a database ``CHECK`` - see ``0025`` and the module
    docstring on why legacy rows keep both ``NULL`` legitimately - so the
    *neither* half is a service rule, and a viewer meets it exactly as management
    always has.
    """
    content_id, master = await ready_to_publish(world)
    cut = await add_derivative(world, content_id, actor=world.other)

    with pytest.raises(PrValidationError) as neither:
        await publish(world, content_id, actor=world.other)
    assert neither.value.details["reason"] == "output_required"

    with pytest.raises(PrValidationError) as both:
        await publish(world, content_id, submission=master, derivative=cut, actor=world.other)
    assert both.value.details["reason"] == "output_not_both"

    assert await publications_of(world, content_id) == []


# ===========================================================================
# 22-27: THE WORKFLOW STILL DECIDES WHEN
# ===========================================================================


async def test_22_the_first_publication_by_a_viewer_publishes_the_content(
    world: World,
) -> None:
    """Requirement 22. One transaction, both facts, recorded by a plain colleague.

    Not an approval and not a shortcut: the transition is the consequence of the
    fact being recorded, and it is written through the same workflow service by
    ``world.other``, who holds no transition authority of their own.
    """
    content_id, master = await ready_to_publish(world)
    outcome = await world.services.publications.register_publication(
        actor=world.actor(world.other),
        request_id=world.request_id,
        command=RegisterPublicationCommand(
            content_id=content_id,
            channel_id=world.channel_id,
            published_at=NOW,
            production_submission_id=master.id,
            url=POST_URL,
        ),
    )
    assert outcome.was_first is True
    assert outcome.new_stage is PrWorkflowStage.PUBLISHED
    assert await stage_of(world, content_id) is PrWorkflowStage.PUBLISHED


async def test_23_24_a_published_piece_takes_another_posting_and_another(
    world: World,
) -> None:
    """Requirements 23 and 24. Later postings append and move nothing.

    Three channels, three viewers, no assignment anywhere - and the stage is
    whatever it already was. The workflow is not restarted and the content is not
    cloned; a publication is an occurrence, and a piece has as many as it has.
    """
    content_id, master = await ready_to_publish(world)
    await publish(world, content_id, submission=master, actor=world.other)

    second = await unplanned_channel(world, "Kênh thứ hai")
    await publish(
        world,
        content_id,
        channel_id=second.id,
        submission=master,
        actor=world.other,
        at=NOW + timedelta(days=1),
    )
    assert await stage_of(world, content_id) is PrWorkflowStage.PUBLISHED

    # A third, later still. Step 1F.2.3f.5 retired ``MEASURED``, which this test
    # used to force the content into for its second half - the point was never
    # the stage but that a piece keeps taking postings without the workflow
    # restarting, and ``PUBLISHED`` is where such a piece now stays.
    third = await unplanned_channel(world, "Kênh thứ ba")
    await publish(
        world,
        content_id,
        channel_id=third.id,
        submission=master,
        actor=world.other,
        at=NOW + timedelta(days=2),
    )
    assert await stage_of(world, content_id) is PrWorkflowStage.PUBLISHED
    assert len(await publications_of(world, content_id)) == 3


async def test_25_archived_still_refuses_a_new_publication(world: World) -> None:
    """Requirement 25. Broadening who may record did not broaden when.

    ``ARCHIVED`` is not in ``PUBLISHABLE_STAGES`` and a viewer does not get a
    softer version of that, which is the failure mode this requirement exists to
    exclude: an open create rule must not become an open mutability rule.
    """
    content_id, master = await ready_to_publish(world)
    await force_stage(world, content_id, PrWorkflowStage.ARCHIVED)

    with pytest.raises(PrWorkflowTransitionError):
        await publish(world, content_id, submission=master, actor=world.other)
    assert await publications_of(world, content_id) == []
    assert PrWorkflowStage.ARCHIVED not in PUBLISHABLE_STAGES


async def test_26_a_pre_publish_stage_is_still_refused(world: World) -> None:
    """Requirement 26. The stage list is the domain's, read and not restated.

    ``INTERNAL_REVIEW`` is a real point in the walk - the piece is produced and
    waiting on a verdict - and it is not publishable. The refusal names the
    canonical set rather than a list this module wrote for itself.
    """
    content_id, master = await ready_to_publish(world)
    await force_stage(world, content_id, PrWorkflowStage.INTERNAL_REVIEW)

    with pytest.raises(PrWorkflowTransitionError) as raised:
        await publish(world, content_id, submission=master, actor=world.other)
    assert raised.value.details["allowed"] == sorted(stage.value for stage in PUBLISHABLE_STAGES)


async def test_27_a_refused_publication_leaves_no_half_written_state(world: World) -> None:
    """Requirement 27. Validate everything, then write - so a refusal costs nothing.

    A bad URL is the last thing checked before the insert, which makes it the
    sharpest test of the order: no publication row, no stage change, no
    transition event and no audit line. The piece is still ``READY_TO_PUBLISH``
    and can be published properly a second later.
    """
    content_id, master = await ready_to_publish(world)

    with pytest.raises(PrValidationError):
        await publish(
            world, content_id, submission=master, actor=world.other, url="javascript:alert(1)"
        )

    assert await publications_of(world, content_id) == []
    assert await stage_of(world, content_id) is PrWorkflowStage.READY_TO_PUBLISH

    recorded = await publish(world, content_id, submission=master, actor=world.other)
    assert recorded.url == POST_URL
    assert await stage_of(world, content_id) is PrWorkflowStage.PUBLISHED


async def test_the_publication_url_keeps_its_strict_validator(world: World) -> None:
    """Requirement 52 of the specification. Two fields, two rules, no drift.

    Step 1F.2.3f.2 made *asset locations* flexible so a producer could paste
    ``M:\\XAY KENH\\video.mp4``. The publication URL is a different concept - a
    public post somebody clicks - and it kept ``http(s)`` with a real host. This
    asserts the two did not converge while the authorization around them changed.
    """
    content_id, master = await ready_to_publish(world)

    for bad in ("M:\\XAY KENH\\video.mp4", "/volume1/media/post.html", "ftp://x.test/a"):
        with pytest.raises(PrValidationError):
            await publish(world, content_id, submission=master, actor=world.other, url=bad)
    assert await publications_of(world, content_id) == []


# ===========================================================================
# 28-34: CREATING IS BROAD, CORRECTING IS THE ROW'S OWN
# ===========================================================================


async def test_28_30_the_viewer_who_recorded_it_corrects_their_own_row(
    world: World,
) -> None:
    """Requirements 28, 29 and 30. Link, instant and note - the transcription.

    The three fields that are a *transcription* of the same event, corrected by
    the ordinary member who recorded it and who holds no administrative
    capability at all.
    """
    content_id, master = await ready_to_publish(world)
    recorded = await publish(world, content_id, submission=master, actor=world.other)
    later = NOW + timedelta(hours=3)

    fixed = await world.services.publications.update_publication(
        actor=world.actor(world.other),
        request_id=world.request_id,
        command=UpdatePublicationCommand(
            publication_id=recorded.id,
            url="https://www.tiktok.com/@apexmed/video/7399999999999999999",
            published_at=later,
            note="sửa link sau khi đổi tên kênh",
        ),
    )
    assert fixed.url.endswith("7399999999999999999")
    assert _instant(fixed.published_at) == later
    assert fixed.note == "sửa link sau khi đổi tên kênh"


async def test_31_an_unrelated_member_may_not_edit_somebody_elses(world: World) -> None:
    """Requirement 31. **Create is broad; edit is the row owner's.**

    The single most important boundary in this step. Both people may record a
    publication against this content - that is requirement 1 - and exactly one of
    them may correct this row. The refusal is per row and reads
    ``publisher_user_id``, so it does not care who owns the content.
    """
    content_id, master = await ready_to_publish(world)
    recorded = await publish(world, content_id, submission=master, actor=world.other)
    stranger = await outsider(world, "Ngô Người Lạ")

    assert await world.services.publications.may_record_publication(
        world.actor(stranger), await world.reload(content_id)
    )
    assert not await world.services.publications.may_edit_publication(
        world.actor(stranger), recorded
    )
    with pytest.raises(PrPermissionDeniedError) as raised:
        await world.services.publications.update_publication(
            actor=world.actor(stranger),
            request_id=world.request_id,
            command=UpdatePublicationCommand(publication_id=recorded.id, url=POST_URL),
        )
    assert raised.value.details["reason"] == "not_the_publisher"


async def test_32_management_still_corrects_anybodys(world: World) -> None:
    """Requirement 32. ``PR_PUBLICATION_REGISTER`` keeps the reach it had."""
    content_id, master = await ready_to_publish(world)
    recorded = await publish(world, content_id, submission=master, actor=world.other)

    fixed = await world.services.publications.update_publication(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        command=UpdatePublicationCommand(publication_id=recorded.id, note="ghi chú của quản trị"),
    )
    assert fixed.note == "ghi chú của quản trị"


async def test_33_34_the_channel_and_the_output_stay_frozen_for_everyone(
    world: World,
) -> None:
    """Requirements 33 and 34. Unrepresentable rather than refused.

    ``UpdatePublicationCommand`` has no ``channel_id`` and no output reference,
    and neither does the request model - so there is no code path that mutates
    publication lineage for a viewer, for its publisher or for an owner. A row
    entered against the wrong channel is reversed and re-entered, which leaves
    both facts visible.
    """
    assert not hasattr(UpdatePublicationCommand, "channel_id")
    fields = set(UpdatePublicationCommand.__dataclass_fields__)
    assert fields == {"publication_id", "url", "published_at", "note"}

    from meobot.api.schemas.pr import UpdatePublicationRequest

    assert set(UpdatePublicationRequest.model_fields) == {"url", "published_at", "note"}

    content_id, master = await ready_to_publish(world)
    recorded = await publish(world, content_id, submission=master, actor=world.other)
    channel_before, output_before = recorded.channel_id, recorded.production_submission_id
    await world.services.publications.update_publication(
        actor=world.actor(world.other),
        request_id=world.request_id,
        command=UpdatePublicationCommand(publication_id=recorded.id, note="đổi ghi chú thôi"),
    )
    await world.session.refresh(recorded)
    assert recorded.channel_id == channel_before
    assert recorded.production_submission_id == output_before


# ===========================================================================
# 35-40: TAKING ONE BACK IS STILL MANAGEMENT
# ===========================================================================


async def test_35_36_neither_the_creator_nor_a_stranger_may_reverse(world: World) -> None:
    """Requirements 35 and 36. Ownership of a row is not authority over history.

    Recording a posting is a statement about your own work. Reversing one edits
    the distribution history and can move the content's stage back, which is a
    decision about the record - so the person who recorded it has exactly the
    same answer as the colleague who did not.
    """
    content_id, master = await ready_to_publish(world)
    recorded = await publish(world, content_id, submission=master, actor=world.other)
    stranger = await outsider(world, "Đỗ Không Liên Quan")

    for person in (world.other, stranger):
        assert not await world.services.publications.may_reverse_publication(world.actor(person))
        with pytest.raises(PrPermissionDeniedError):
            await world.services.publications.reverse_publication(
                actor=world.actor(person),
                request_id=world.request_id,
                publication_id=recorded.id,
            )

    await world.session.refresh(recorded)
    assert recorded.status is PrPublicationStatus.PUBLISHED
    assert await stage_of(world, content_id) is PrWorkflowStage.PUBLISHED


async def test_37_38_management_reversal_still_works_and_still_compensates(
    world: World,
) -> None:
    """Requirements 37 and 38. Step 1F.2.3f.1's safety, untouched.

    The last active publication of a piece that was published by it: the row is
    marked ``REVERSED`` rather than deleted, and the stage is compensated back to
    ``READY_TO_PUBLISH``. That a member recorded it in the first place changes
    neither half.
    """
    content_id, master = await ready_to_publish(world)
    recorded = await publish(world, content_id, submission=master, actor=world.other)

    outcome = await world.services.publications.reverse_publication(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        publication_id=recorded.id,
    )
    assert outcome.stage_reverted is True
    await world.session.refresh(recorded)
    assert recorded.status is PrPublicationStatus.REVERSED
    assert len(await publications_of(world, content_id)) == 1
    assert await stage_of(world, content_id) is PrWorkflowStage.READY_TO_PUBLISH


async def test_39_a_measured_publication_is_still_protected(world: World) -> None:
    """Requirement 39. The metrics restriction is unchanged by who recorded it.

    A publication with observations of its **own** is not reversed at all - a
    hard ``409``, not a soft outcome - because taking it back would leave
    measurements claiming numbers for a posting the record says never happened.
    Step 1F.2.3f.1's rule, and who recorded the row has never been part of it.
    """
    from tests.unit.test_pr_publication_corrections import add_metric

    content_id, master = await ready_to_publish(world)
    recorded = await publish(world, content_id, submission=master, actor=world.other)
    await add_metric(world, recorded)

    with pytest.raises(PrConflictError) as raised:
        await world.services.publications.reverse_publication(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            publication_id=recorded.id,
        )
    assert raised.value.details["reason"] == "has_metrics"
    await world.session.refresh(recorded)
    assert recorded.status is PrPublicationStatus.PUBLISHED
    assert await stage_of(world, content_id) is PrWorkflowStage.PUBLISHED


async def test_40_a_reversed_member_publication_still_locks_its_output(
    world: World,
) -> None:
    """Requirement 40. No "member publication" exception to historical protection.

    A publication rows written by a plain viewer freezes what it points at
    exactly as one written by an owner does, and reversing it does not lift the
    freeze - which is the half people get wrong. The label stays editable; the
    location does not.
    """
    content_id, _ = await ready_to_publish(world)
    cut = await add_derivative(world, content_id, actor=world.other)
    recorded = await publish(world, content_id, derivative=cut, actor=world.other)
    await world.services.publications.reverse_publication(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        publication_id=recorded.id,
    )

    from meobot.application.pr_content_asset_service import UpdateDerivativeCommand

    with pytest.raises(PrConflictError):
        await world.services.content_assets.update_derivative(
            actor=world.actor(world.other),
            request_id=world.request_id,
            command=UpdateDerivativeCommand(
                derivative_id=cut.id, location="https://drive.google.com/file/d/1Moved/view"
            ),
        )


async def test_a_member_publication_still_blocks_a_permanent_delete(world: World) -> None:
    """Requirement 21 of the specification's hard-delete boundary.

    Who created the row does not matter, and neither does its status: a reversed
    publication counts too. Asserted here because "the boundary is about the
    creator" is precisely the wrong lesson to draw from an opened create rule.
    """
    content_id, master = await ready_to_publish(world)
    recorded = await publish(world, content_id, submission=master, actor=world.other)
    await world.services.publications.reverse_publication(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        publication_id=recorded.id,
    )

    with pytest.raises(PrPublishedContentError) as raised:
        await world.services.lifecycle.delete_content(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            content_id=content_id,
        )
    assert raised.value.details["reason"] == "has_publications"


# ===========================================================================
# 41-45: WHO ADDED THE LINK
# ===========================================================================


async def test_41_42_the_row_and_the_audit_both_name_the_member(world: World) -> None:
    """Requirements 41 and 42, and the question this has to keep answering.

    *"Ai add link đăng bài?"* is answered two ways that must agree:
    ``publisher_user_id`` on the row, and the actor on the audit event. Both are
    the ordinary member who recorded it - not the content's owner, and not
    whoever happens to hold an administrative permission.
    """
    content_id, master = await ready_to_publish(world)
    recorded = await publish(world, content_id, submission=master, actor=world.other)

    assert recorded.publisher_user_id == world.other.id
    rows = await world.session.execute(
        select(AuditLog).where(AuditLog.entity_id == str(recorded.id))
    )
    entry = next(row for row in rows.scalars().all() if row.action == "pr.publication.registered")
    assert entry.actor_user_id == world.other.id
    assert entry.after_data["url"] == POST_URL


async def test_43_44_a_later_management_edit_keeps_the_original_attribution(
    world: World,
) -> None:
    """Requirements 43 and 44. Two different people, two different columns.

    ``publisher_user_id`` says who published; the ``pr.publication.updated``
    actor says who corrected the transcription. An administrator fixing a typo
    must not become the person who posted the video.
    """
    content_id, master = await ready_to_publish(world)
    recorded = await publish(world, content_id, submission=master, actor=world.other)

    await world.services.publications.update_publication(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        command=UpdatePublicationCommand(publication_id=recorded.id, note="quản trị sửa"),
    )
    await world.session.refresh(recorded)
    assert recorded.publisher_user_id == world.other.id

    rows = await world.session.execute(
        select(AuditLog).where(AuditLog.entity_id == str(recorded.id))
    )
    updated = [row for row in rows.scalars().all() if row.action == "pr.publication.updated"]
    assert [row.actor_user_id for row in updated] == [world.owner.id]


async def test_45_the_read_model_shows_the_recorder_by_name(world: World) -> None:
    """Requirement 45. A name on screen, never a UUID.

    The row carries ``publisher_user_id`` and ``/api/pr/people`` carries the
    names - the one join every screen in this app already does, which is what
    *"Người ghi nhận"* renders. Asserted end to end here because a publication
    recorded by a member must resolve to that member and not to a blank.

    The two per-row flags travel with it, and they are the ones the panel draws
    *Sửa* and *Hoàn tác* from: this member may correct their own row and may not
    take it back.
    """
    content_id, master = await ready_to_publish(world)
    await publish(world, content_id, submission=master, actor=world.other)

    world.act_as(world.other)
    response = world.client.get(f"/api/pr/contents/{content_id}/publications")
    assert response.status_code == 200, response.text
    row = response.json()[0]
    assert row["publisher_user_id"] == str(world.other.id)
    assert row["can_edit"] is True
    assert row["can_reverse"] is False

    people = world.client.get("/api/pr/people")
    assert people.status_code == 200, people.text
    names = {person["user_id"]: person["full_name"] for person in people.json()}
    assert names[row["publisher_user_id"]] == world.other.full_name


# ===========================================================================
# 46-50: THE ROUTE IS THE CONTROL, NOT THE SCREEN
# ===========================================================================


async def test_46_a_viewer_posts_directly_and_succeeds(world: World) -> None:
    """Requirement 46. No UI involved, and no old channel permission either."""
    content_id, master = await ready_to_publish(world)
    channel = await unplanned_channel(world, "Kênh gọi thẳng API")
    world.act_as(world.other)

    response = await post_publication(
        world,
        content_id,
        channel_id=str(channel.id),
        published_at=NOW.isoformat(),
        production_submission_id=str(master.id),
        url=POST_URL,
    )
    assert response.status_code == 201, response.text
    rows = await publications_of(world, content_id)
    assert [row.publisher_user_id for row in rows] == [world.other.id]


async def test_47_a_non_viewer_posting_directly_is_refused(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requirement 47. The backend is the boundary, over HTTP as well.

    The permission is removed for the duration of the request, which is the only
    honest way to build "somebody who cannot view this" out of four roles that
    all can - see requirement 8.
    """
    content_id, master = await ready_to_publish(world)
    world.act_as(world.other)
    monkeypatch.setattr("meobot.domain.pr.policy.has_permission", lambda role, permission: False)

    response = await post_publication(
        world,
        content_id,
        channel_id=str(world.channel_id),
        published_at=NOW.isoformat(),
        production_submission_id=str(master.id),
        url=POST_URL,
    )
    assert response.status_code == 403, response.text
    assert await publications_of(world, content_id) == []


async def test_48_49_forged_channel_and_forged_output_are_refused_over_http(
    world: World,
) -> None:
    """Requirements 48 and 49. A viewer may record; a viewer may not invent.

    Two forged references from a legitimately authenticated viewer - a channel id
    that is in no table, and an output belonging to a different content item.
    Different status codes because they are different failures: one does not
    exist, the other exists and is not this piece's.
    """
    content_id, master = await ready_to_publish(world)
    other_id, other_master = await ready_to_publish(world)
    world.act_as(world.other)

    forged_channel = await post_publication(
        world,
        content_id,
        channel_id=str(uuid.uuid4()),
        published_at=NOW.isoformat(),
        production_submission_id=str(master.id),
        url=POST_URL,
    )
    assert forged_channel.status_code == 404, forged_channel.text

    forged_output = await post_publication(
        world,
        content_id,
        channel_id=str(world.channel_id),
        published_at=NOW.isoformat(),
        production_submission_id=str(other_master.id),
        url=POST_URL,
    )
    assert forged_output.status_code == 422, forged_output.text
    assert await publications_of(world, content_id) == []
    assert await publications_of(world, other_id) == []


async def test_50_the_offer_and_the_route_agree_for_everybody(world: World) -> None:
    """Requirement 50. A hidden button is not a rule, and a shown one is not either.

    ``RECORD_PUBLICATION`` is offered to every one of the five people, at a
    publishable stage and not at ``ARCHIVED`` - and the offer is the server's own
    predicate rather than a role string the client could have guessed. The
    frontend hides the *"+ Thêm kênh đã đăng"* button on exactly this, which is
    why the two must be the same answer.
    """
    content_id, _ = await ready_to_publish(world)
    for person in (world.owner, world.lead, world.head, world.member, world.other):
        assert "RECORD_PUBLICATION" in await actions_for(world, content_id, actor=person)

    await force_stage(world, content_id, PrWorkflowStage.ARCHIVED)
    for person in (world.owner, world.other):
        assert "RECORD_PUBLICATION" not in await actions_for(world, content_id, actor=person)


async def test_an_employee_role_is_never_read_as_the_answer(world: World) -> None:
    """Requirement 9 of the specification, from the other side.

    The five people in this file cover four roles, and the create answer is the
    same for all of them because the gate is a *permission* - ``script.read`` -
    and not a role comparison. This asserts the shape rather than the outcome:
    an ``EMPLOYEE`` and an ``OWNER`` get the same boolean out of the predicate.
    """
    content_id, _ = await ready_to_publish(world)
    content = await world.reload(content_id)
    assert world.other.role is Role.EMPLOYEE
    assert world.owner.role is Role.OWNER
    assert await world.services.publications.may_record_publication(
        world.actor(world.other), content
    ) == await world.services.publications.may_record_publication(world.actor(world.owner), content)
