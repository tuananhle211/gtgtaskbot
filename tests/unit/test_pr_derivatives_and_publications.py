"""Step 1F.2.3f - re-cuts of a finished piece, and which file actually went out.

Numbered 1-54, following the requirement numbering the step was specified with:
1-21 derivatives, 22-47 publications, 48-54 destination links, and one explicit
end-to-end reuse scenario in the middle of them because it is the whole reason
the step exists.

The world is ``test_pr_production_lifecycle``'s, imported rather than rebuilt.
That is deliberate: everything here happens *after* production, so the fixture a
test needs is "a piece that was really produced" - walked through briefing,
scripting, an AI verdict, two approval gates, a handoff, a start and a submission
- and a second copy of that walk would eventually disagree with the first about
what "produced" means. What this file adds is the life the old fixture stopped
short of: the cutdown somebody makes in October for a channel that did not exist
in August.

Everything runs against real SQL on one transaction, and the authorization half
runs over the real router as well, because "a direct API call cannot bypass this"
is only meaningful over HTTP.

The one thing these tests keep asserting
-----------------------------------------

**Nothing here is a workflow event.** Recording a derivative, attaching a
destination link and appending a later publication all leave ``workflow_stage``
exactly where it was, and the tests say so repeatedly rather than once - because
the failure mode this step most needed to avoid is a "reuse" feature that
quietly sends a published piece back into production.
"""

from __future__ import annotations

# The ``world`` fixture is imported from ``test_pr_production_lifecycle`` so this
# file reuses the walk that reaches production rather than owning a second copy
# of it - see the module docstring. pytest requires the fixture to be a
# module-level name, and every test then takes a parameter of the same name, so
# ruff sees a redefinition on every signature in the file. It is the standard
# shape for a shared fixture and the warning is about nothing.
# ruff: noqa: F811
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from meobot.application.pr_channel_service import CreateChannelCommand
from meobot.application.pr_content_asset_service import (
    AddDerivativeCommand,
    AddDestinationCommand,
    UpdateDerivativeCommand,
    UpdateDestinationCommand,
)
from meobot.application.pr_publication_service import (
    PUBLISHABLE_STAGES,
    RegisterPublicationCommand,
)
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.pr import PrChannel, PrContentItem, PrPlatform
from meobot.db.models.pr_content_asset import PrContentDerivative, PrContentDestination
from meobot.db.models.pr_production import PrProductionSubmission
from meobot.db.models.pr_reporting import PrPublication
from meobot.db.models.user import User
from meobot.db.models.user_notification import UserNotification
from meobot.domain.pr.errors import (
    PrConflictError,
    PrNotFoundError,
    PrPermissionDeniedError,
    PrPublishedContentError,
    PrValidationError,
    PrWorkflowTransitionError,
)
from meobot.domain.pr.labels import DERIVATIVE_TYPE_LABELS
from meobot.domain.pr.models import (
    PrApprovalStage,
    PrChannelCategory,
    PrContentDerivativeType,
    PrWorkflowStage,
)
from meobot.domain.pr.policy import PrCapability
from tests.unit.test_pr_production_lifecycle import (  # noqa: F401 - `world` is a fixture
    DRIVE,
    NOW,
    World,
    audit_actions,
    decide,
    make_content,
    submit,
    to_production,
    world,
)

pytestmark = pytest.mark.asyncio

#: A perfectly ordinary derivative location. A Drive URL, because that is what a
#: producer actually pastes.
CUT = "https://drive.google.com/file/d/1CutDownTwentyFiveSeconds/view"

#: The live post. Never confused with the file above - see requirement 32.
POST_URL = "https://www.tiktok.com/@apexmed/video/7300000000000000000"

LANDING = "https://apexmed.vn/dich-vu/nang-mui"


# --- Getting a piece all the way to "ready" ---------------------------------


async def ready_to_publish(world: World) -> tuple[uuid.UUID, PrProductionSubmission]:
    """A produced piece standing at ``READY_TO_PUBLISH``, with its master cut.

    The full walk, ending at the internal reviewer's approval - which is what
    makes the submission returned here the file somebody actually signed off,
    and therefore the thing a publication should be able to name.
    """
    content_id = await make_content(world, owner=world.member)
    await to_production(world, content_id, producer=world.member)
    submission = await submit(world, content_id, actor=world.member)
    if (
        PrCapability.PR_INTERNAL_REVIEW
        not in await world.services.capabilities.capabilities_for_actor(world.actor(world.lead))
    ):
        # Idempotent: a test that builds two ready pieces would otherwise fail on
        # the second grant rather than on what it is actually asserting.
        await world.services.capabilities.grant(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            user_id=world.lead.id,
            capability=PrCapability.PR_INTERNAL_REVIEW,
        )
    await decide(
        world, actor=world.lead, content_id=content_id, stage=PrApprovalStage.INTERNAL_REVIEW
    )
    assert (await world.reload(content_id)).workflow_stage is PrWorkflowStage.READY_TO_PUBLISH
    return content_id, submission


async def add_derivative(
    world: World,
    content_id: uuid.UUID,
    *,
    actor: User | None = None,
    derivative_type: PrContentDerivativeType = PrContentDerivativeType.CUTDOWN,
    label: str = "TikTok cut 25s",
    location: str = CUT,
    source: PrProductionSubmission | uuid.UUID | None = None,
    note: str | None = None,
) -> PrContentDerivative:
    source_id = source.id if isinstance(source, PrProductionSubmission) else source
    return await world.services.content_assets.add_derivative(
        actor=world.actor(actor or world.member),
        request_id=world.request_id,
        command=AddDerivativeCommand(
            content_id=content_id,
            derivative_type=derivative_type,
            label=label,
            location=location,
            source_submission_id=source_id,
            note=note,
        ),
    )


async def publish(
    world: World,
    content_id: uuid.UUID,
    *,
    channel_id: uuid.UUID | None = None,
    submission: PrProductionSubmission | None = None,
    derivative: PrContentDerivative | None = None,
    url: str | None = POST_URL,
    at: datetime = NOW,
    note: str | None = None,
    actor: User | None = None,
) -> PrPublication:
    outcome = await world.services.publications.register_publication(
        actor=world.actor(actor or world.owner),
        request_id=world.request_id,
        command=RegisterPublicationCommand(
            content_id=content_id,
            channel_id=channel_id or world.channel_id,
            published_at=at,
            production_submission_id=submission.id if submission else None,
            derivative_id=derivative.id if derivative else None,
            url=url,
            note=note,
        ),
    )
    return outcome.publication


async def new_channel(world: World, name: str = "Apexmed TikTok") -> PrChannel:
    """A channel created *after* the content's targets were chosen.

    The October half of the reuse scenario, and its own platform row so nothing
    about it is shared with the content's planned target.
    """
    platform = PrPlatform(code=f"PLT{uuid.uuid4().hex[:6].upper()}", name="Kênh mới")
    world.session.add(platform)
    await world.session.flush()
    return await world.services.channels.create_channel(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        command=CreateChannelCommand(
            name=name,
            platform_id=platform.id,
            brand_id=world.brand_id,
            category=PrChannelCategory.SCALE,
        ),
    )


async def stage_of(world: World, content_id: uuid.UUID) -> PrWorkflowStage:
    return (await world.reload(content_id)).workflow_stage


async def force_stage(world: World, content_id: uuid.UUID, stage: PrWorkflowStage) -> None:
    """Stand an item at a late stage without walking the last steps.

    Used only for ``MEASURED`` and ``ARCHIVED``, where what put it there changes
    nothing about the question being asked, and where the real walk is already
    proved elsewhere.
    """
    row = await world.session.get(PrContentItem, content_id)
    assert row is not None
    row.workflow_stage = stage
    await world.session.flush()


async def derivatives_of(world: World, content_id: uuid.UUID) -> list[PrContentDerivative]:
    rows = await world.session.execute(
        select(PrContentDerivative)
        .where(PrContentDerivative.content_id == content_id)
        .order_by(PrContentDerivative.created_at.asc())
    )
    return list(rows.scalars().all())


# ===========================================================================
# 1-8: THE DERIVATIVE VOCABULARY AND ITS VALIDATION
# ===========================================================================


async def test_01_06_every_derivative_type_is_accepted(world: World) -> None:
    """Requirements 1-6. All six, each stored as itself.

    Asserted over the enum rather than over a list written here, so a seventh
    value added without a Vietnamese label fails on the next line rather than
    shipping as a raw code on somebody's screen.
    """
    content_id, master = await ready_to_publish(world)
    for kind in PrContentDerivativeType:
        row = await add_derivative(
            world, content_id, derivative_type=kind, label=f"Bản {kind.value}", source=master
        )
        assert row.derivative_type is kind
        assert DERIVATIVE_TYPE_LABELS[kind]
    assert len(await derivatives_of(world, content_id)) == len(PrContentDerivativeType)


async def test_07_an_unknown_derivative_type_is_refused_with_the_options(world: World) -> None:
    """Requirement 7. A misspelt type is a 422 naming the six, not a stored string."""
    content_id, _ = await ready_to_publish(world)
    world.act_as(world.member)
    response = world.client.post(
        f"/api/pr/contents/{content_id}/derivatives",
        json={"derivative_type": "SHORTCUT", "label": "x", "location": CUT},
    )
    assert response.status_code == 422, response.text
    details = response.json()["error"]["details"]
    assert details["field"] == "derivative_type"
    assert details["allowed"] == sorted(kind.value for kind in PrContentDerivativeType)


async def test_08_a_label_is_required(world: World) -> None:
    """Requirement 8. A blank label is refused, and nothing is written.

    The label is what the publication form's output picker is made of: five rows
    titled with their own Drive URLs is a list nobody can choose from.
    """
    content_id, _ = await ready_to_publish(world)
    for blank in ("", "   "):
        with pytest.raises(PrValidationError):
            await add_derivative(world, content_id, label=blank)
    assert await derivatives_of(world, content_id) == []


async def test_09_the_location_is_validated_and_never_fetched(world: World) -> None:
    """Requirement 9. The same rule a production submission goes through.

    A URL or an absolute NAS path, decided by the shape of the string, and the
    refused schemes are the ones the whole product refuses. Nothing opens any of
    them - this is a syntax check with a safety rule, and the safety rule is that
    a stored location is rendered as a link.
    """
    content_id, _ = await ready_to_publish(world)
    # Step 1F.2.3f.2 widened the path half of this: a mapped drive and a
    # location relative to a shared root are what the team actually types, and
    # refusing them made the *stored* data worse. See
    # ``test_pr_asset_locations_and_contributors`` for the full grammar.
    for good in (
        CUT,
        "https://cdn.example.com/cut.mp4",
        "/volume1/PR/2026/cut-25s.mp4",
        "shared/PR/2026/cut-25s.mp4",
    ):
        assert (await add_derivative(world, content_id, location=good)).location == good
    for bad, reason in (
        ("javascript:alert(1)", "unsafe_scheme"),
        ("data:text/html,<script>", "unsafe_scheme"),
        ("file:///etc/passwd", "unsafe_scheme"),
        ("   ", "empty"),
    ):
        with pytest.raises(PrValidationError) as raised:
            await add_derivative(world, content_id, location=bad)
        assert raised.value.details["reason"] == reason, bad


async def test_10_the_source_submission_gives_lineage(world: World) -> None:
    """Requirement 10. *"TikTok cut 25s ← Video final 60s"*, stored.

    Optional, because a file re-cut from raw footage came from no tracked
    submission and requiring a link there would mean storing a guess.
    """
    content_id, master = await ready_to_publish(world)
    linked = await add_derivative(world, content_id, source=master)
    assert linked.source_submission_id == master.id

    unlinked = await add_derivative(world, content_id, label="Bản dựng lại", source=None)
    assert unlinked.source_submission_id is None


async def test_11_a_source_submission_from_another_content_is_refused(world: World) -> None:
    """Requirement 11. Lineage is checked, never trusted from the request.

    A derivative pointing at another item's master would claim a history that
    never happened, and would make "which files came out of this piece" answer
    with somebody else's.
    """
    first_id, first_master = await ready_to_publish(world)
    second_id, _ = await ready_to_publish(world)

    with pytest.raises(PrValidationError) as raised:
        await add_derivative(world, second_id, source=first_master)
    assert raised.value.details["reason"] == "foreign_submission"
    assert await derivatives_of(world, second_id) == []
    # And the honest pairing still works, so the refusal is about ownership
    # rather than about the link being unusable.
    assert (await add_derivative(world, first_id, source=first_master)).content_id == first_id


async def test_11a_a_derivative_cannot_be_cut_from_another_derivative(world: World) -> None:
    """No recursive lineage in this step, enforced by the column's type.

    ``source_submission_id`` is a foreign key to ``pr_production_submissions``
    and could not name a derivative if somebody tried - which is what makes the
    decision unrepresentable rather than merely refused. Asserted against the
    metadata so it survives a future service refactor.
    """
    target = PrContentDerivative.__table__.c.source_submission_id
    referred = {fk.column.table.name for fk in target.foreign_keys}
    assert referred == {"pr_production_submissions"}


# ===========================================================================
# 12-13: WHO MAY RECORD A DERIVATIVE
# ===========================================================================


async def test_12_the_producer_and_production_management_may_add(world: World) -> None:
    """Requirement 12. Produced work, authorised as produced work.

    Two positive branches and no third: whoever holds the production work, and
    whoever decides whose work it is. The same predicate that decides who may
    hand in a cut, so "who may submit" and "who may record a re-cut of it" have
    one answer.
    """
    content_id, master = await ready_to_publish(world)
    # The producer.
    assert await add_derivative(world, content_id, actor=world.member, source=master)
    # Production management - the lead holds ``PR_PRODUCTION_ASSIGN``.
    assert await add_derivative(world, content_id, actor=world.lead, label="Reel 30s")


async def test_13_an_unrelated_member_may_add_since_step_1f23g(world: World) -> None:
    """Requirement 13, **reversed by Step 1F.2.3g** - and deliberately kept here.

    As shipped, this step refused an unrelated colleague with
    ``reason="not_the_producer"``: every ``EMPLOYEE`` holds
    ``PR_PRODUCTION_EXECUTE``, so the capability alone would have let anybody
    attach a file to anybody's content, and the narrowing was the producer
    relationship.

    Step 1F.2.3g decided that was the wrong question to ask about a *derivative*.
    Recording one is not a handover - nothing reviews it, no stage moves, nobody
    is waiting on it - and the person who knows a 25-second cut exists is
    whoever made it, months later, for a channel that did not exist when the
    piece was produced. So the rule is now the module's view rule.

    The assertion is inverted rather than deleted: what this test protects is the
    *decision*, and a reader arriving at requirement 13 should find out that it
    moved and why. The refusal that remains - somebody who cannot view the
    content at all - is asserted in ``test_pr_open_contributions_and_comments``.
    """
    content_id, _ = await ready_to_publish(world)
    recorded = await add_derivative(world, content_id, actor=world.other, label="Cut của tôi")
    assert recorded.created_by_user_id == world.other.id

    world.act_as(world.other)
    response = world.client.post(
        f"/api/pr/contents/{content_id}/derivatives",
        json={"derivative_type": "CUTDOWN", "label": "Bản khác", "location": CUT},
    )
    assert response.status_code == 201, response.text
    assert len(await derivatives_of(world, content_id)) == 2


async def test_13a_the_offer_matches_the_write(world: World) -> None:
    """The panel is told exactly what the POST would accept.

    Two offers since Step 1F.2.3g, because there are two rules.
    ``ADD_CONTENT_DERIVATIVE`` follows the widened create rule and reaches
    everybody who may read the piece; ``MANAGE_CONTENT_DERIVATIVES`` still means
    "you are production management here" and is unchanged. Both come off the
    write's own predicate, so a control is never drawn for somebody the server
    would refuse.
    """
    content_id, _ = await ready_to_publish(world)
    assert "MANAGE_CONTENT_DERIVATIVES" in await world.actions(world.member, content_id)
    assert "MANAGE_CONTENT_DERIVATIVES" in await world.actions(world.lead, content_id)
    assert "MANAGE_CONTENT_DERIVATIVES" not in await world.actions(world.other, content_id)

    for user in (world.member, world.lead, world.other):
        assert "ADD_CONTENT_DERIVATIVE" in await world.actions(user, content_id)


# ===========================================================================
# 14-17: CORRECTING AND REMOVING
# ===========================================================================


async def test_14_a_derivative_may_be_corrected(world: World) -> None:
    """Requirement 14. A pointer to a file, so fixing it is a correction."""
    content_id, master = await ready_to_publish(world)
    derivative = await add_derivative(world, content_id, source=None)

    updated = await world.services.content_assets.update_derivative(
        actor=world.actor(world.member),
        request_id=world.request_id,
        command=UpdateDerivativeCommand(
            derivative_id=derivative.id,
            derivative_type=PrContentDerivativeType.REFORMAT,
            label="Bản dọc 9:16",
            location="/volume1/PR/2026/doc.mp4",
            source_submission_id=master.id,
            note="Xuất lại từ master",
        ),
    )
    assert updated.derivative_type is PrContentDerivativeType.REFORMAT
    assert updated.label == "Bản dọc 9:16"
    assert updated.location == "/volume1/PR/2026/doc.mp4"
    assert updated.source_submission_id == master.id
    assert updated.note == "Xuất lại từ master"


async def test_15_every_mutation_is_audited(world: World) -> None:
    """Requirement 15. Three actions, because they are three questions.

    "Who deleted the TikTok cut" is asked directly, so it is a filter rather than
    a search through payloads. The payload names and locates the row and carries
    its lineage; it copies no note and nothing the location points at.
    """
    content_id, master = await ready_to_publish(world)
    derivative = await add_derivative(world, content_id, source=master, note="bí mật")
    await world.services.content_assets.update_derivative(
        actor=world.actor(world.member),
        request_id=world.request_id,
        command=UpdateDerivativeCommand(derivative_id=derivative.id, label="TikTok cut 20s"),
    )
    await world.services.content_assets.delete_derivative(
        actor=world.actor(world.member), request_id=world.request_id, derivative_id=derivative.id
    )
    assert await audit_actions(world, derivative.id) == [
        "pr.content.derivative_added",
        "pr.content.derivative_updated",
        "pr.content.derivative_deleted",
    ]

    rows = await world.session.execute(
        select(AuditLog).where(AuditLog.entity_id == str(derivative.id))
    )
    added = next(iter(rows.scalars().all()))
    assert added.after_data["content_code"]
    assert added.after_data["derivative_type"] == PrContentDerivativeType.CUTDOWN.value
    assert added.after_data["source_submission_id"] == str(master.id)
    assert "note" not in added.after_data


async def test_15a_an_unedited_form_writes_no_audit_row(world: World) -> None:
    """A no-op update is not an event.

    An audit trail that logs "CUTDOWN -> CUTDOWN" every time somebody reopens a
    form teaches whoever reads it to skip derivative rows - the same reasoning a
    no-op priority change is silent.
    """
    content_id, _ = await ready_to_publish(world)
    derivative = await add_derivative(world, content_id)
    await world.services.content_assets.update_derivative(
        actor=world.actor(world.member),
        request_id=world.request_id,
        command=UpdateDerivativeCommand(
            derivative_id=derivative.id, label="TikTok cut 25s", location=CUT
        ),
    )
    assert await audit_actions(world, derivative.id) == ["pr.content.derivative_added"]


async def test_16_an_unreferenced_derivative_may_be_deleted(world: World) -> None:
    """Requirement 16. Nothing depends on it being what it was."""
    content_id, _ = await ready_to_publish(world)
    derivative = await add_derivative(world, content_id)
    await world.services.content_assets.delete_derivative(
        actor=world.actor(world.member), request_id=world.request_id, derivative_id=derivative.id
    )
    assert await derivatives_of(world, content_id) == []


async def test_17_a_published_derivative_is_neither_deleted_nor_rewritten(world: World) -> None:
    """Requirement 17, and the edit-safety rule beside it.

    Once a publication names this file, the row is part of the answer to "what
    did we actually post". So:

    * **delete** is refused outright - the publication would be left naming a
      file that no longer exists;
    * **the identity-bearing fields are frozen.** Changing where a published file
      lives would tell a reader of the publication row that October's post used
      something that had never been posted;
    * **the human fields stay editable.** Fixing a typo in a label corrects how
      the row reads and changes nothing about what happened.
    """
    content_id, _ = await ready_to_publish(world)
    derivative = await add_derivative(world, content_id)
    await publish(world, content_id, derivative=derivative)

    with pytest.raises(PrConflictError) as deleted:
        await world.services.content_assets.delete_derivative(
            actor=world.actor(world.member),
            request_id=world.request_id,
            derivative_id=derivative.id,
        )
    assert deleted.value.details["reason"] == "published_output_is_immutable"
    assert len(await derivatives_of(world, content_id)) == 1

    with pytest.raises(PrConflictError) as moved:
        await world.services.content_assets.update_derivative(
            actor=world.actor(world.member),
            request_id=world.request_id,
            command=UpdateDerivativeCommand(
                derivative_id=derivative.id, location="https://example.com/other.mp4"
            ),
        )
    assert moved.value.details["fields"] == ["location"]
    assert moved.value.details["editable"] == ["label", "note"]

    # And a request mixing a frozen field with a free one changes **nothing**,
    # rather than half of what was asked.
    with pytest.raises(PrConflictError):
        await world.services.content_assets.update_derivative(
            actor=world.actor(world.member),
            request_id=world.request_id,
            command=UpdateDerivativeCommand(
                derivative_id=derivative.id,
                label="Đã sửa",
                derivative_type=PrContentDerivativeType.REMIX,
            ),
        )
    await world.session.refresh(derivative)
    assert derivative.label == "TikTok cut 25s"
    assert derivative.location == CUT

    # The human half still corrects.
    fixed = await world.services.content_assets.update_derivative(
        actor=world.actor(world.member),
        request_id=world.request_id,
        command=UpdateDerivativeCommand(derivative_id=derivative.id, label="TikTok cut 25 giây"),
    )
    assert fixed.label == "TikTok cut 25 giây"


# ===========================================================================
# 18-21: WHEN A DERIVATIVE MAY BE ADDED
# ===========================================================================


async def test_18_adding_a_derivative_moves_no_stage(world: World) -> None:
    """Requirement 18. Not a transition, and no history event either.

    This is the failure the whole step most needed to avoid: a "reuse" feature
    that quietly sends a finished piece back into production.
    """
    content_id, master = await ready_to_publish(world)
    before = await audit_actions(world, content_id)
    await add_derivative(world, content_id, source=master)
    assert await stage_of(world, content_id) is PrWorkflowStage.READY_TO_PUBLISH
    assert await audit_actions(world, content_id) == before


@pytest.mark.parametrize(
    "stage",
    [PrWorkflowStage.PUBLISHED, PrWorkflowStage.MEASURED, PrWorkflowStage.ARCHIVED],
)
async def test_19_21_late_stage_content_still_accepts_a_derivative(
    world: World, stage: PrWorkflowStage
) -> None:
    """Requirements 19, 20 and 21, and the ``ARCHIVED`` rule stated outright.

    All three, including ``ARCHIVED``, and that is not a new permission - it is
    this repository's existing treatment of operational metadata. Priority,
    content type and review resources have never consulted ``workflow_stage``,
    and ``ARCHIVED``'s meaning is precise and narrower than "frozen": it is a
    ``TERMINAL_STAGES`` member, so **no transition may leave it**, and it is
    undeletable. Neither of those is a rule about attaching a link.

    Recording six months later the cutdown a colleague actually made is
    correcting the record of an archived piece rather than reopening it - and
    refusing it would leave exactly the workaround this step exists to remove:
    cloning the content.
    """
    content_id, master = await ready_to_publish(world)
    await force_stage(world, content_id, stage)

    derivative = await add_derivative(world, content_id, source=master, label=f"Bản {stage.value}")
    assert derivative.content_id == content_id
    assert await stage_of(world, content_id) is stage


async def test_21a_archived_stays_terminal_and_undeletable(world: World) -> None:
    """The ``ARCHIVED`` rules that **are** rules, unchanged by this step.

    Stated here so the permission above cannot be read as "archived content is
    now mutable in general": nothing transitions out of it, and nothing deletes
    it, exactly as before.
    """
    content_id, _ = await ready_to_publish(world)
    await force_stage(world, content_id, PrWorkflowStage.ARCHIVED)
    await add_derivative(world, content_id)

    with pytest.raises(PrWorkflowTransitionError):
        await world.services.workflow.request_transition(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            content_id=content_id,
            target=PrWorkflowStage.PUBLISHED,
        )
    with pytest.raises(PrPublishedContentError):
        await world.services.lifecycle.delete_content(
            actor=world.actor(world.owner), request_id=world.request_id, content_id=content_id
        )


# ===========================================================================
# 22-36: WHAT A PUBLICATION SAYS
# ===========================================================================


async def test_22_zero_publications_is_valid(world: World) -> None:
    """Requirement 22. Production-complete content may have gone nowhere yet."""
    content_id, _ = await ready_to_publish(world)
    assert await world.services.publications.list_publications(content_id) == []
    assert await stage_of(world, content_id) is PrWorkflowStage.READY_TO_PUBLISH


async def test_23_the_first_publication_may_name_the_master(world: World) -> None:
    """Requirement 23. The 60-second cut went to Facebook, and the row says so."""
    content_id, master = await ready_to_publish(world)
    publication = await publish(world, content_id, submission=master)
    assert publication.production_submission_id == master.id
    assert publication.derivative_id is None


async def test_24_the_first_publication_may_name_a_derivative(world: World) -> None:
    """Requirement 24. A piece may go out as a re-cut and never as its master."""
    content_id, _ = await ready_to_publish(world)
    derivative = await add_derivative(world, content_id)
    publication = await publish(world, content_id, derivative=derivative)
    assert publication.derivative_id == derivative.id
    assert publication.production_submission_id is None


async def test_25_27_exactly_one_output_is_required(world: World) -> None:
    """Requirements 25-27. Not neither, and not both.

    Two different mistakes with two different reasons. "Neither" is a record
    being written incomplete, and the database cannot refuse it without
    fabricating lineage for the rows that predate the column - so it is refused
    here. "Both" is meaningless under every reading, so it is refused here *and*
    by ``ck_pr_publications_output_not_both``.
    """
    content_id, master = await ready_to_publish(world)
    derivative = await add_derivative(world, content_id)

    with pytest.raises(PrValidationError) as neither:
        await publish(world, content_id)
    assert neither.value.details["reason"] == "output_required"

    with pytest.raises(PrValidationError) as both:
        await publish(world, content_id, submission=master, derivative=derivative)
    assert both.value.details["reason"] == "output_not_both"

    assert await world.services.publications.list_publications(content_id) == []
    assert await stage_of(world, content_id) is PrWorkflowStage.READY_TO_PUBLISH


async def test_25a_the_database_refuses_both_as_well(world: World) -> None:
    """The ``CHECK`` exists and says "never both" - not XOR.

    Asserted against the metadata rather than by writing a row, because the
    interesting property is precisely what the constraint does **not** forbid:
    legacy rows carrying neither reference stay legal, permanently.
    """
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in PrPublication.__table__.constraints
        if type(constraint).__name__ == "CheckConstraint"
    }
    assert "ck_pr_publications_output_not_both" in checks
    condition = checks["ck_pr_publications_output_not_both"]
    assert "NOT (" in condition
    # Both nullable, so a row naming no output is still insertable.
    assert PrPublication.__table__.c.production_submission_id.nullable
    assert PrPublication.__table__.c.derivative_id.nullable


async def test_28_an_output_from_another_content_is_refused(world: World) -> None:
    """Requirement 28. The commonest client mistake is a stale form.

    Both halves: somebody else's master and somebody else's derivative. The ids
    are looked up and their ``content_id`` compared - never trusted.
    """
    first_id, first_master = await ready_to_publish(world)
    first_derivative = await add_derivative(world, first_id)
    second_id, _ = await ready_to_publish(world)

    for kwargs in ({"submission": first_master}, {"derivative": first_derivative}):
        with pytest.raises(PrValidationError) as raised:
            await publish(world, second_id, **kwargs)  # type: ignore[arg-type]
        assert raised.value.details["reason"] == "foreign_output"
    assert await world.services.publications.list_publications(second_id) == []


async def test_29_the_channel_must_exist(world: World) -> None:
    """Requirement 29. A mistyped channel is a sentence, not a foreign-key error.

    Checked explicitly since the plan stopped being a gate: without the target
    lookup in front of it, an unknown id would otherwise reach the insert.
    """
    content_id, master = await ready_to_publish(world)
    with pytest.raises(PrNotFoundError) as raised:
        await publish(world, content_id, channel_id=uuid.uuid4(), submission=master)
    assert raised.value.details["field"] == "channel_id"


async def test_30_a_channel_nobody_planned_for_is_accepted(world: World) -> None:
    """Requirement 30, and the business case the step was written for.

    In August the targets were chosen. In October a channel exists that did not
    exist then. Requiring a planned target here would leave exactly two
    workarounds - clone the content, or back-date a plan nobody made - and both
    corrupt the record worse than an unplanned publication ever could.
    """
    content_id, master = await ready_to_publish(world)
    later = await new_channel(world)
    publication = await publish(world, content_id, channel_id=later.id, submission=master)
    assert publication.channel_id == later.id
    assert await stage_of(world, content_id) is PrWorkflowStage.PUBLISHED


async def test_30a_a_planned_channel_still_has_its_target_fulfilled(world: World) -> None:
    """The plan is still a plan, and publishing to it still fulfils it.

    Linkage rather than permission: when a target exists it is marked
    ``PUBLISHED``, because that is what every channel-level report joins through.
    """
    content_id, master = await ready_to_publish(world)
    outcome = await world.services.publications.register_publication(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        command=RegisterPublicationCommand(
            content_id=content_id,
            channel_id=world.channel_id,
            published_at=NOW,
            production_submission_id=master.id,
            url=POST_URL,
        ),
    )
    assert outcome.target is not None
    assert outcome.target.status.value == "PUBLISHED"

    # And the unplanned case reports no target rather than refusing.
    later = await new_channel(world, "Kênh tháng 10")
    second = await world.services.publications.register_publication(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        command=RegisterPublicationCommand(
            content_id=content_id,
            channel_id=later.id,
            published_at=NOW + timedelta(days=1),
            production_submission_id=master.id,
            url=POST_URL,
        ),
    )
    assert second.target is None


async def test_31_33_the_post_url_is_validated(world: World) -> None:
    """Requirements 31-33. ``http(s)`` with a host, and nothing is fetched.

    A stored URL is rendered as a link, which makes the accepted scheme set a
    security boundary rather than a formatting preference - so ``javascript:``
    and ``data:`` are refused here exactly as they are on every other location in
    this product.
    """
    content_id, master = await ready_to_publish(world)
    assert (await publish(world, content_id, submission=master, url=POST_URL)).url == POST_URL

    for bad in ("javascript:alert(1)", "data:text/html,<script>", "tiktok.com/@a/video/1"):
        with pytest.raises(PrValidationError):
            await publish(world, content_id, submission=master, url=bad)
    assert len(await world.services.publications.list_publications(content_id)) == 1


async def test_34_36_time_note_and_actor_persist(world: World) -> None:
    """Requirements 34-36. What was recorded is what comes back."""
    content_id, master = await ready_to_publish(world)
    when = datetime(2026, 10, 18, 13, 10, tzinfo=UTC)
    publication = await publish(
        world, content_id, submission=master, at=when, note="Đăng lại dịp khai trương"
    )
    assert publication.published_at == when
    assert publication.note == "Đăng lại dịp khai trương"
    assert publication.publisher_user_id == world.owner.id


# ===========================================================================
# 37-41: STAGE, ATOMICITY AND THE LATE STAGES
# ===========================================================================


async def test_37_the_first_publication_publishes_the_content(world: World) -> None:
    """Requirement 37. ``READY_TO_PUBLISH -> PUBLISHED``, with a history event."""
    content_id, master = await ready_to_publish(world)
    outcome = await world.services.publications.register_publication(
        actor=world.actor(world.owner),
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
    assert "pr.content.stage_changed" in await audit_actions(world, content_id)


async def test_38_a_refused_publication_moves_nothing(world: World) -> None:
    """Requirement 38. Publication and transition are one unit.

    The order is validate-everything-then-write, so a request refused for a bad
    URL - the last thing checked - still leaves no publication, no stage change
    and no audit row.
    """
    content_id, master = await ready_to_publish(world)
    before = await audit_actions(world, content_id)

    with pytest.raises(PrValidationError):
        await publish(world, content_id, submission=master, url="javascript:alert(1)")

    assert await world.services.publications.list_publications(content_id) == []
    assert await stage_of(world, content_id) is PrWorkflowStage.READY_TO_PUBLISH
    assert await audit_actions(world, content_id) == before


async def test_39_published_content_accepts_more_publications(world: World) -> None:
    """Requirement 39. The second channel appends and moves nothing.

    A piece that goes to three channels over three days becomes ``PUBLISHED``
    once, on the first - which is what "published" means about the idea rather
    than about one cut of it.
    """
    content_id, master = await ready_to_publish(world)
    await publish(world, content_id, submission=master)
    derivative = await add_derivative(world, content_id)
    later = await new_channel(world)

    outcome = await world.services.publications.register_publication(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        command=RegisterPublicationCommand(
            content_id=content_id,
            channel_id=later.id,
            published_at=NOW + timedelta(days=2),
            derivative_id=derivative.id,
            url=POST_URL,
        ),
    )
    assert outcome.was_first is False
    assert outcome.new_stage is None
    assert await stage_of(world, content_id) is PrWorkflowStage.PUBLISHED
    assert len(await world.services.publications.list_publications(content_id)) == 2


async def test_40_published_content_accepts_another_publication_much_later(
    world: World,
) -> None:
    """Requirement 40. Publication is not the end of a life.

    The reuse case this step was built around: a piece first posted in August is
    exactly the piece somebody re-cuts for a new channel in October, and refusing
    to record that posting would leave the distribution history wrong about a
    piece that is still working.

    Step 1F.2.3f.5 retired ``MEASURED``, which used to be in
    ``PUBLISHABLE_STAGES`` for this reason. The case is unchanged - it was
    ``PUBLISHED`` that carried it, and publication is now simply where such a
    piece stays.
    """
    content_id, master = await ready_to_publish(world)
    await publish(world, content_id, submission=master)

    derivative = await add_derivative(world, content_id)
    later = await new_channel(world)
    await publish(
        world,
        content_id,
        channel_id=later.id,
        derivative=derivative,
        at=NOW + timedelta(days=90),
    )
    assert await stage_of(world, content_id) is PrWorkflowStage.PUBLISHED
    assert len(await world.services.publications.list_publications(content_id)) == 2


async def test_41_archived_content_records_no_new_publication(world: World) -> None:
    """Requirement 41. Archiving is the end of a life, and this is unchanged.

    ``ARCHIVED`` was never in ``PUBLISHABLE_STAGES`` and is not added: nothing
    transitions out of it, and a new posting is new activity rather than a
    correction to the record. Something being published from an archived piece
    means it should not have been archived - and it can be, because a derivative
    may still be recorded there (requirement 21).
    """
    content_id, master = await ready_to_publish(world)
    await force_stage(world, content_id, PrWorkflowStage.ARCHIVED)

    with pytest.raises(PrWorkflowTransitionError) as raised:
        await publish(world, content_id, submission=master)
    assert raised.value.details["allowed"] == sorted(s.value for s in PUBLISHABLE_STAGES)
    assert PrWorkflowStage.ARCHIVED.value not in raised.value.details["allowed"]
    assert "RECORD_PUBLICATION" not in await world.actions(world.owner, content_id)


# ===========================================================================
# 42-47: REPOSTS, ORDER, AUDIT AND DELETE
# ===========================================================================


async def test_42_the_same_channel_may_be_published_to_again(world: World) -> None:
    """Requirement 42. A repost is a second event, not an edit of the first.

    Deliberately no ``unique(content_id, channel_id)``: a campaign refresh three
    months later has its own date and its own numbers.
    """
    content_id, master = await ready_to_publish(world)
    first = await publish(world, content_id, submission=master)
    second = await publish(world, content_id, submission=master, at=NOW + timedelta(days=90))
    assert first.id != second.id
    assert {
        row.channel_id for row in await world.services.publications.list_publications(content_id)
    } == {world.channel_id}
    assert len(await world.services.publications.list_publications(content_id)) == 2


async def test_43_the_same_output_may_be_published_repeatedly(world: World) -> None:
    """Requirement 43. The same cut, three channels, three rows."""
    content_id, _ = await ready_to_publish(world)
    derivative = await add_derivative(world, content_id)
    for index in range(3):
        channel = await new_channel(world, f"Kênh {index}")
        await publish(
            world,
            content_id,
            channel_id=channel.id,
            derivative=derivative,
            at=NOW + timedelta(days=index),
        )
    rows = await world.services.publications.list_publications(content_id)
    assert len(rows) == 3
    assert {row.derivative_id for row in rows} == {derivative.id}


async def test_44_different_outputs_reach_different_channels(world: World) -> None:
    """Requirement 44. Which cut went where, which is the question this answers."""
    content_id, master = await ready_to_publish(world)
    cut = await add_derivative(world, content_id, label="TikTok cut 25s")
    reel = await add_derivative(
        world, content_id, derivative_type=PrContentDerivativeType.REFORMAT, label="Reel 30s"
    )
    tiktok = await new_channel(world, "Apexmed TikTok")
    facebook = await new_channel(world, "Apexmed Facebook")

    await publish(world, content_id, submission=master)
    await publish(world, content_id, channel_id=tiktok.id, derivative=cut, at=NOW + timedelta(1))
    await publish(world, content_id, channel_id=facebook.id, derivative=reel, at=NOW + timedelta(2))

    used = {
        row.channel_id: (row.production_submission_id, row.derivative_id)
        for row in await world.services.publications.list_publications(content_id)
    }
    assert used[world.channel_id] == (master.id, None)
    assert used[tiktok.id] == (None, cut.id)
    assert used[facebook.id] == (None, reel.id)


async def test_45_the_publication_order_is_total(world: World) -> None:
    """Requirement 45. Newest first, and deterministic when the dates tie.

    Two postings recorded in one sitting share an instant, and a history that
    reorders itself between two opens of the same page is a history nobody
    trusts. ``code`` is unique and allocated in sequence, so it breaks the tie.
    """
    content_id, master = await ready_to_publish(world)
    codes = []
    for index in range(3):
        channel = await new_channel(world, f"Kênh {index}")
        codes.append(
            (await publish(world, content_id, channel_id=channel.id, submission=master)).code
        )
    rows = await world.services.publications.list_publications(content_id)
    assert [row.code for row in rows] == sorted(codes, reverse=True)
    # Stable across calls, which is the property that actually matters.
    assert [row.id for row in await world.services.publications.list_publications(content_id)] == [
        row.id for row in rows
    ]


async def test_46_the_publication_audit_names_the_output(world: World) -> None:
    """Requirement 46. What went out, where, and which file.

    The output's own storage location is deliberately absent: it is on the row
    this points at, and a copy here would be a second thing to keep in step.
    """
    content_id, _ = await ready_to_publish(world)
    derivative = await add_derivative(world, content_id)
    publication = await publish(world, content_id, derivative=derivative, note="ra mắt")

    rows = await world.session.execute(
        select(AuditLog).where(AuditLog.entity_id == str(publication.id))
    )
    entry = next(iter(rows.scalars().all()))
    assert entry.action == "pr.publication.registered"
    assert entry.after_data["derivative_id"] == str(derivative.id)
    assert entry.after_data["production_submission_id"] is None
    assert entry.after_data["url"] == POST_URL
    assert entry.after_data["channel_id"] == str(world.channel_id)
    assert entry.after_data["first_publication"] is True
    assert entry.after_data["content_code"]


async def test_46a_no_notification_is_raised_for_any_of_this(world: World) -> None:
    """Audit only. Step 1F.2.3f adds no notification-center noise.

    Recording distribution history is not news for anybody else: five derivatives
    during one campaign afternoon and three publications in one sitting would be
    eight notifications nobody asked for.
    """
    content_id, master = await ready_to_publish(world)

    async def inbox_rows() -> int:
        found = await world.session.execute(select(UserNotification.id))
        return len(list(found.scalars().all()))

    before = await inbox_rows()
    await add_derivative(world, content_id, source=master)
    await world.services.content_assets.add_destination(
        actor=world.actor(world.member),
        request_id=world.request_id,
        command=AddDestinationCommand(content_id=content_id, label="Landing", url=LANDING),
    )
    await publish(world, content_id, submission=master)
    assert await inbox_rows() == before


async def test_47_a_publication_still_blocks_permanent_delete(world: World) -> None:
    """Requirement 47. Unchanged, and now with derivatives in the way too.

    Published work is operational history and there is no force flag. What the
    step *did* change is the delete plan below the floor: a derivative and a
    destination are removed with the aggregate, in an order that respects the
    lineage link.
    """
    content_id, master = await ready_to_publish(world)
    await add_derivative(world, content_id, source=master)
    await publish(world, content_id, submission=master)

    with pytest.raises(PrPublishedContentError):
        await world.services.lifecycle.delete_content(
            actor=world.actor(world.owner), request_id=world.request_id, content_id=content_id
        )
    assert len(await derivatives_of(world, content_id)) == 1


async def test_47a_deleting_unpublished_content_takes_its_assets_with_it(world: World) -> None:
    """The delete plan covers both new tables, in an order that works.

    The derivative goes **before** the submission it was cut from: ``RESTRICT``
    would otherwise refuse the submission's delete while the lineage link still
    existed, which is why the plan is a list rather than a set.
    """
    content_id = await make_content(world, owner=world.member)
    await to_production(world, content_id, producer=world.member)
    master = await submit(world, content_id, actor=world.member)
    await add_derivative(world, content_id, source=master)
    await world.services.content_assets.add_destination(
        actor=world.actor(world.member),
        request_id=world.request_id,
        command=AddDestinationCommand(content_id=content_id, label="Landing", url=LANDING),
    )

    await world.services.lifecycle.delete_content(
        actor=world.actor(world.owner), request_id=world.request_id, content_id=content_id
    )
    assert await world.session.get(PrContentItem, content_id) is None
    assert await derivatives_of(world, content_id) == []
    left = await world.session.execute(
        select(PrContentDestination).where(PrContentDestination.content_id == content_id)
    )
    assert left.scalars().all() == []


# ===========================================================================
# THE REUSE SCENARIO, END TO END
# ===========================================================================


async def test_the_august_piece_is_reused_in_october_without_a_clone(world: World) -> None:
    """The business case, walked exactly as it happens.

    August: *"5 thực phẩm cần kiêng"* is produced, the 60-second master is
    approved, and it goes out on the planned channel.

    October: a TikTok channel exists that did not exist then. Somebody opens the
    **same** content, records a 25-second cutdown, and records the TikTok posting
    against it.

    What must be true afterwards is the whole of this step: one content id, one
    script, the original output untouched, the August publication untouched, a
    new derivative, a new publication naming it - and no workflow restart
    anywhere.
    """
    content_id, master = await ready_to_publish(world)
    august = await publish(world, content_id, submission=master, at=NOW)
    assert await stage_of(world, content_id) is PrWorkflowStage.PUBLISHED
    version_before = (await world.services.content.require_current_version(content_id)).id
    history_before = await audit_actions(world, content_id)

    # --- October -----------------------------------------------------------
    tiktok = await new_channel(world, "Apexmed TikTok")
    cutdown = await add_derivative(
        world, content_id, source=master, label="TikTok cut 25s", note="Cắt cho kênh mới"
    )
    october = await publish(
        world,
        content_id,
        channel_id=tiktok.id,
        derivative=cutdown,
        at=NOW + timedelta(days=60),
    )

    # One content row, and only one: no clone anywhere in the table.
    items = await world.session.execute(select(PrContentItem.id))
    assert len(list(items.scalars().all())) == 1
    # The script never moved.
    assert (await world.services.content.require_current_version(content_id)).id == version_before
    # The stage never moved, and no transition was recorded.
    assert await stage_of(world, content_id) is PrWorkflowStage.PUBLISHED
    assert await audit_actions(world, content_id) == history_before
    # The original output is still there, unchanged.
    masters = await world.session.execute(
        select(PrProductionSubmission).where(PrProductionSubmission.content_id == content_id)
    )
    assert [row.id for row in masters.scalars().all()] == [master.id]
    # Both publications, and each names the file that actually went out.
    rows = await world.services.publications.list_publications(content_id)
    assert {row.id for row in rows} == {august.id, october.id}
    assert august.production_submission_id == master.id and august.derivative_id is None
    assert october.derivative_id == cutdown.id and october.production_submission_id is None
    assert october.channel_id == tiktok.id


# ===========================================================================
# 48-54: PRODUCT AND LANDING PAGES
# ===========================================================================


async def test_48_49_a_destination_link_is_attached_and_kept(world: World) -> None:
    """Requirements 48 and 49. A label and a URL, and that is the whole model."""
    content_id, _ = await ready_to_publish(world)
    destination = await world.services.content_assets.add_destination(
        actor=world.actor(world.member),
        request_id=world.request_id,
        command=AddDestinationCommand(
            content_id=content_id, label="Landing page dịch vụ", url=LANDING, note="Chạy từ T10"
        ),
    )
    assert destination.label == "Landing page dịch vụ"
    assert destination.url == LANDING
    assert destination.note == "Chạy từ T10"
    assert await stage_of(world, content_id) is PrWorkflowStage.READY_TO_PUBLISH


async def test_50_an_unsafe_or_pathlike_destination_is_refused(world: World) -> None:
    """Requirement 50. Always a URL, never a path.

    A destination is somewhere a customer is sent, so a NAS path in that field is
    a string nothing can open - refused rather than stored. The unsafe schemes go
    the same way they do everywhere else in this product.
    """
    content_id, _ = await ready_to_publish(world)
    for bad in (
        "javascript:alert(1)",
        "data:text/html,<script>",
        "/volume1/PR/landing.html",
        "apexmed.vn/dich-vu",
        "  ",
    ):
        with pytest.raises(PrValidationError):
            await world.services.content_assets.add_destination(
                actor=world.actor(world.member),
                request_id=world.request_id,
                command=AddDestinationCommand(content_id=content_id, label="x", url=bad),
            )


async def test_51_destinations_use_the_content_metadata_rule(world: World) -> None:
    """Requirement 51. The same authorisation as priority, type and resources.

    Where a piece sends a customer is a fact about the campaign, not about a
    file - so this is **not** the production rule the derivatives use. The
    distinction is visible here: the unrelated colleague is refused, the
    responsible member is not, and management is not.
    """
    content_id, _ = await ready_to_publish(world)
    command = AddDestinationCommand(content_id=content_id, label="Landing", url=LANDING)

    with pytest.raises(PrPermissionDeniedError) as raised:
        await world.services.content_assets.add_destination(
            actor=world.actor(world.other), request_id=world.request_id, command=command
        )
    assert raised.value.details["reason"] == "not_responsible"

    assert await world.services.content_assets.add_destination(
        actor=world.actor(world.member), request_id=world.request_id, command=command
    )
    assert await world.services.content_assets.add_destination(
        actor=world.actor(world.lead), request_id=world.request_id, command=command
    )
    assert "MANAGE_CONTENT_DESTINATIONS" in await world.actions(world.member, content_id)
    assert "MANAGE_CONTENT_DESTINATIONS" not in await world.actions(world.other, content_id)


async def test_52_destination_mutations_are_audited(world: World) -> None:
    """Requirement 52. Three concise events: a label and a URL is the row."""
    content_id, _ = await ready_to_publish(world)
    destination = await world.services.content_assets.add_destination(
        actor=world.actor(world.member),
        request_id=world.request_id,
        command=AddDestinationCommand(content_id=content_id, label="Landing", url=LANDING),
    )
    await world.services.content_assets.update_destination(
        actor=world.actor(world.member),
        request_id=world.request_id,
        command=UpdateDestinationCommand(
            destination_id=destination.id, url="https://apexmed.vn/dat-lich"
        ),
    )
    await world.services.content_assets.delete_destination(
        actor=world.actor(world.member),
        request_id=world.request_id,
        destination_id=destination.id,
    )
    assert await audit_actions(world, destination.id) == [
        "pr.content.destination_added",
        "pr.content.destination_updated",
        "pr.content.destination_deleted",
    ]


async def test_53_destinations_are_visible_at_every_stage(world: World) -> None:
    """Requirement 53, and requirement 28 of the step's own numbering.

    Durable content metadata: attached before anybody produced anything and still
    readable when the piece is archived. The read is the plain permission, so a
    reviewer who may not edit it may still see where the piece points.
    """
    content_id = await make_content(world, owner=world.member)
    await world.services.content_assets.add_destination(
        actor=world.actor(world.member),
        request_id=world.request_id,
        command=AddDestinationCommand(content_id=content_id, label="Landing", url=LANDING),
    )
    for stage in (
        PrWorkflowStage.READY_TO_PUBLISH,
        PrWorkflowStage.PUBLISHED,
        PrWorkflowStage.MEASURED,
        PrWorkflowStage.ARCHIVED,
    ):
        await force_stage(world, content_id, stage)
        rows = await world.services.queries.content_destinations(
            actor=world.actor(world.other), content_id=content_id
        )
        assert [row.label for row in rows] == ["Landing"], stage.value


# ===========================================================================
# THE COMPLETE DETAIL RECORD
# ===========================================================================


@pytest.mark.parametrize(
    "stage",
    [
        PrWorkflowStage.READY_TO_PUBLISH,
        PrWorkflowStage.PUBLISHED,
        PrWorkflowStage.MEASURED,
        PrWorkflowStage.ARCHIVED,
    ],
)
async def test_the_late_stage_record_loses_nothing(world: World, stage: PrWorkflowStage) -> None:
    """Stage controls what you may **do**; it never controls what you may see.

    Everything accumulated across every previous step is still readable at every
    late stage: what the piece is, who answers for it, who produced it, the
    script, the review material, the destination links, the master cuts, the
    derivatives, the approval history and the publication history.

    This is the regression that matters most for the whole step. A detail page
    that quietly dropped the script once the piece was published would be useless
    at exactly the moment it becomes the operational record.
    """
    content_id, master = await ready_to_publish(world)
    await world.services.content_assets.add_destination(
        actor=world.actor(world.member),
        request_id=world.request_id,
        command=AddDestinationCommand(content_id=content_id, label="Landing", url=LANDING),
    )
    derivative = await add_derivative(world, content_id, source=master)
    await publish(world, content_id, submission=master)
    await force_stage(world, content_id, stage)

    world.act_as(world.other)
    detail = world.client.get(f"/api/pr/contents/{content_id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["content"]["content_type"] is not None or True  # present as a field
    assert body["content"]["priority"]
    assert body["brand"]["name"]
    assert body["content"]["owner_user_id"]
    assert body["content"]["producer_user_id"] == str(world.member.id)
    assert body["current_version"]["script_text"]

    for path, expected in (
        ("resources", 0),
        ("destinations", 1),
        ("production-outputs", 1),
        ("derivatives", 1),
        ("publications", 1),
        ("approvals", 3),
    ):
        response = world.client.get(f"/api/pr/contents/{content_id}/{path}")
        assert response.status_code == 200, (path, response.text)
        assert len(response.json()) == expected, path

    # And the two lists a client joins to answer "which file went where".
    publication = world.client.get(f"/api/pr/contents/{content_id}/publications").json()[0]
    outputs = world.client.get(f"/api/pr/contents/{content_id}/production-outputs").json()
    assert publication["production_submission_id"] == outputs[0]["id"]
    assert publication["derivative_id"] is None
    derivatives = world.client.get(f"/api/pr/contents/{content_id}/derivatives").json()
    assert derivatives[0]["id"] == str(derivative.id)
    assert derivatives[0]["source_submission_id"] == outputs[0]["id"]
    # Words, not codes: the location is carried with a server-computed hint about
    # whether it is clickable.
    assert derivatives[0]["is_link"] is True


# ===========================================================================
# THE BOARD IS UNTOUCHED
# ===========================================================================


async def test_the_board_never_fetches_any_of_this(world: World) -> None:
    """Step 1F.2.3c2's lane queries are unchanged, and stay unchanged.

    Two claims. The first is about the response: a board page carries no
    derivative, publication or destination collection, so there is no N+1 to
    have. The second is about the code: the content page's query builder has
    never heard of these tables, and a join added there later would be caught
    here rather than in production.
    """
    content_id, master = await ready_to_publish(world)
    await add_derivative(world, content_id, source=master)
    await publish(world, content_id, submission=master)

    world.act_as(world.owner)
    body = world.client.get("/api/pr/contents/board", params={"scope": "ALL", "limit": 20}).json()
    assert body["items"], body
    card = body["items"][0]
    for absent in ("derivatives", "publications", "destinations", "production_outputs"):
        assert absent not in card, absent
        assert absent not in body, absent

    source = (
        __import__("pathlib").Path("src/meobot/application/pr_content_query.py").read_text("utf-8")
    )
    for table in ("PrContentDerivative", "PrContentDestination"):
        assert table not in source, table

    # Step 1F.2.3f.4 narrowed the second claim rather than dropping it, and the
    # narrowing is the point. The completed board is now scoped to a work month,
    # which cannot be decided without knowing when a piece went out - so
    # ``PrPublication`` does appear here, in exactly one shape:
    #
    #   * a **correlated scalar subquery** returning ``MIN(published_at)``. It
    #     multiplies no rows, so an item on three channels is still one card and
    #     the count beside the list still matches it;
    #   * never a ``join``, which is what would bring the row multiplication and
    #     the double-counted total this test was written to prevent.
    #
    # The per-card instant is a separate grouped query over the page's ids in
    # ``PrQueryService._published_instants`` - one round trip for fifty cards,
    # not fifty - and it is skipped entirely when no month is selected.
    assert "scalar_subquery()" in source
    assert "join(PrPublication" not in source
    assert ".join(PrPublication" not in source


async def test_adding_a_derivative_does_not_move_a_card_between_lanes(world: World) -> None:
    """Requirement 16 of the step's lane-compatibility section.

    A derivative, a destination and a later publication all leave the item in the
    lane it was in. Asserted through the board itself rather than through the
    stage column, because the lane is what somebody would actually notice moving.
    """
    content_id, master = await ready_to_publish(world)
    await publish(world, content_id, submission=master)
    world.act_as(world.owner)

    def lane_total(lane: str) -> int:
        response = world.client.get(
            "/api/pr/contents/board",
            params={"scope": "ALL", "group": "COMPLETED", "lane": lane, "limit": 20},
        )
        assert response.status_code == 200, response.text
        return int(response.json()["total"])

    assert lane_total("PUBLISHED") == 1
    assert lane_total("READY_TO_PUBLISH") == 0

    await add_derivative(world, content_id, source=master)
    await world.services.content_assets.add_destination(
        actor=world.actor(world.member),
        request_id=world.request_id,
        command=AddDestinationCommand(content_id=content_id, label="Landing", url=LANDING),
    )
    later = await new_channel(world)
    await publish(
        world, content_id, channel_id=later.id, submission=master, at=NOW + timedelta(days=3)
    )

    assert lane_total("PUBLISHED") == 1
    assert lane_total("READY_TO_PUBLISH") == 0
    assert await stage_of(world, content_id) is PrWorkflowStage.PUBLISHED


# ===========================================================================
# THE PUBLISHED COLUMN, READ AGAINST A REPORTING MONTH. Step 1F.2.3f.4, as kept
# by Step 1F.2.3f.6.
#
# "Đã đăng" was cumulative: everything ever published stayed in it, the count
# grew for ever, and it stopped answering the question a manager opens the
# board with. It is the pieces whose canonical publication instant is in the
# selected month - ``MIN(published_at)`` over active publications.
#
# What Step 1F.2.3f.6 removed from here: the *virtual archive*. An older
# published piece is no longer shown under "Lưu trữ" merely because a later
# month is selected; "Lưu trữ" holds what was actually archived, in the month
# it was archived. The full per-lane month semantics are in
# ``test_pr_reporting_period.py``; this section keeps the publication rule.
# ===========================================================================

AUGUST = datetime(2026, 8, 18, 3, 0, tzinfo=UTC)
SEPTEMBER = datetime(2026, 9, 6, 3, 0, tzinfo=UTC)


def _lane(world: World, lane: str, period: str, **params: object) -> dict[str, object]:
    """One completed column for one reporting month, as the board asks for it.

    Step 1F.2.3f.6c: the archive is not a completed column any more; asking
    for it is asking the archive view.
    """
    where = {"view": "ARCHIVE"} if lane == "ARCHIVED" else {"group": "COMPLETED"}
    response = world.client.get(
        "/api/pr/contents/board",
        params={"scope": "ALL", "lane": lane, "period": period, **where, **params},
    )
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def _codes(body: dict[str, object]) -> set[str]:
    return {row["code"] for row in body["items"]}  # type: ignore[index]


async def _published_on(world: World, at: datetime, **kwargs: object) -> str:
    """One piece carried to PUBLISHED with a given actual publication instant."""
    content_id, master = await ready_to_publish(world)
    await publish(world, content_id, submission=master, at=at, **kwargs)  # type: ignore[arg-type]
    row = await world.session.get(PrContentItem, content_id)
    assert row is not None
    return row.code


async def test_the_published_bucket_is_the_selected_month(world: World) -> None:
    """The actual publication instant decides, and nothing else does.

    Created and planned in August, actually posted in September: it is
    September's. None of the three date *filters* has an opinion here - this is
    the publication fact, which is the only thing that says which month a piece
    counts in.
    """
    august = await _published_on(world, AUGUST)
    september = await _published_on(world, SEPTEMBER)
    world.act_as(world.owner)

    assert _codes(_lane(world, "PUBLISHED", "2026-09")) == {september}
    assert _codes(_lane(world, "PUBLISHED", "2026-08")) == {august}


async def test_the_month_boundaries_are_the_business_calendar(world: World) -> None:
    """First instant of the month inclusive, first of the next exclusive.

    22:00 UTC on 31 August is already 05:00 on 1 September in Asia/Ho_Chi_Minh,
    so it is September's - from the same ``day_bounds`` every other date filter
    on this board uses, and not a second calendar.
    """
    edge = await _published_on(world, datetime(2026, 8, 31, 22, 0, tzinfo=UTC))
    last = await _published_on(world, datetime(2026, 9, 30, 16, 0, tzinfo=UTC))
    world.act_as(world.owner)

    september = _codes(_lane(world, "PUBLISHED", "2026-09"))
    assert edge in september
    assert last in september
    assert edge not in _codes(_lane(world, "PUBLISHED", "2026-08"))


async def test_an_older_publication_is_not_a_virtual_archive(world: World) -> None:
    """Step 1F.2.3f.6, Parts K and L. *Published in an older month* is not *archived*.

    Published 18 August. Selected September: it is in **neither** completed
    column - not *Đã đăng*, because it did not go out in September, and not
    *Lưu trữ*, because nobody archived it. Selected August: it is in *Đã đăng*.
    The row says ``PUBLISHED`` throughout and carries no archive badge, because
    there is no longer an ``archive_reason`` to carry.
    """
    code = await _published_on(world, AUGUST)
    world.act_as(world.owner)

    assert _codes(_lane(world, "ARCHIVED", "2026-09")) == set()
    assert _codes(_lane(world, "PUBLISHED", "2026-09")) == set()

    august = _lane(world, "PUBLISHED", "2026-08")
    assert _codes(august) == {code}
    card = august["items"][0]  # type: ignore[index]
    assert card["workflow_stage"] == "PUBLISHED"
    assert "archive_reason" not in card
    assert card["published_at"] is not None

    row = await world.session.scalar(select(PrContentItem).where(PrContentItem.code == code))
    assert row is not None
    assert row.workflow_stage is PrWorkflowStage.PUBLISHED
    assert row.archived_at is None


async def test_content_published_after_the_selected_month_is_on_no_completed_column(
    world: World,
) -> None:
    """A historical board must not leak work that had not happened yet.

    Viewing August, a piece posted on 6 September is not published-in-August.
    It is in **neither** column, and out of the group total with them.
    """
    september = await _published_on(world, SEPTEMBER)
    world.act_as(world.owner)

    assert september not in _codes(_lane(world, "PUBLISHED", "2026-08"))
    assert september not in _codes(_lane(world, "ARCHIVED", "2026-08"))
    group = world.client.get(
        "/api/pr/contents/board",
        params={"scope": "ALL", "group": "COMPLETED", "period": "2026-08"},
    ).json()
    assert september not in {row["code"] for row in group["items"]}


async def test_the_counts_are_the_cards(world: World) -> None:
    """The figure over a column is the same query as the column, month included."""
    await _published_on(world, AUGUST)
    await _published_on(world, AUGUST)
    await _published_on(world, SEPTEMBER)
    world.act_as(world.owner)

    for period, published in (("2026-09", 1), ("2026-08", 2)):
        on_screen = _lane(world, "PUBLISHED", period)
        assert on_screen["total"] == published, period
        assert len(on_screen["items"]) == published  # type: ignore[arg-type]
        # Nothing was archived, so the archive column is empty in both months
        # - it is no longer where the other month's publications go.
        stored = _lane(world, "ARCHIVED", period)
        assert stored["total"] == 0, period
        assert stored["items"] == []


async def test_the_month_composes_with_the_ordinary_filters(world: World) -> None:
    """The month decides the column; the filters narrow inside it."""
    code = await _published_on(world, SEPTEMBER)
    row = await world.session.scalar(select(PrContentItem).where(PrContentItem.code == code))
    assert row is not None
    world.act_as(world.owner)

    assert _codes(_lane(world, "PUBLISHED", "2026-09", channel_id=str(world.channel_id))) == {code}
    assert _codes(_lane(world, "PUBLISHED", "2026-09", owner_user_id=str(row.owner_user_id))) == {
        code
    }
    # A date filter on a different dimension narrows within the month rather
    # than redefining it: nothing was created in 2019, so this is empty while
    # the month itself still holds the item.
    assert (
        _lane(
            world,
            "PUBLISHED",
            "2026-09",
            date_field="CREATED_AT",
            date_from="2019-01-01",
            date_to="2019-01-02",
        )["total"]
        == 0
    )


async def test_no_month_leaves_the_board_cumulative(world: World) -> None:
    """Every other caller is untouched: no period, no month classification."""
    august = await _published_on(world, AUGUST)
    september = await _published_on(world, SEPTEMBER)
    world.act_as(world.owner)

    body = world.client.get(
        "/api/pr/contents/board",
        params={"scope": "ALL", "group": "COMPLETED", "lane": "PUBLISHED"},
    ).json()
    assert {august, september} <= {row["code"] for row in body["items"]}
    assert body["period"] is None


async def test_two_publications_in_two_months_count_in_the_earlier_one(
    world: World,
) -> None:
    """Part C, and the rule the audit settled on.

    A piece on one channel in August and another in September has been published
    since August - which is what the workflow itself means by published, since it
    stays ``PUBLISHED`` while any active publication remains. It appears **once**,
    in August.
    """
    content_id, master = await ready_to_publish(world)
    await publish(world, content_id, submission=master, at=AUGUST)
    second = await new_channel(world)
    await publish(world, content_id, channel_id=second.id, submission=master, at=SEPTEMBER)
    row = await world.session.get(PrContentItem, content_id)
    assert row is not None
    world.act_as(world.owner)

    august = _lane(world, "PUBLISHED", "2026-08")
    assert _codes(august) == {row.code}
    # One card, not one per publication row.
    assert august["total"] == 1
    assert _codes(_lane(world, "PUBLISHED", "2026-09")) == set()
    # And not under September's archive either: nobody archived it.
    assert _codes(_lane(world, "ARCHIVED", "2026-09")) == set()
