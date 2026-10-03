"""Step 1F.2.3f.1 - one way to publish, and two ways to correct it.

Numbered 1-24, following the requirement numbering the step was specified with.

Three things are being asserted, and they are one argument in three parts:

* **there is one publish action.** ``READY_TO_PUBLISH -> PUBLISHED`` was a
  ``MANUAL`` edge, so the panel drew a standalone *"Đánh dấu đã đăng"* button and
  the API accepted a bare ``POST /transition``. Both marked a piece published
  **with no record of where it went**, which is precisely the state Step
  1F.2.3f's output reference exists to make impossible;
* **a recorded publication can be corrected**, in the parts that are a
  transcription of the event - the link, the instant, the note - and not in the
  parts that *are* the event;
* **a recorded publication can be taken back**, without being deleted, and the
  content's stage follows only when that is safe.

The world is ``test_pr_production_lifecycle``'s, and the helpers are Step
1F.2.3f's, for the reason that file already gives: everything here happens to a
piece that was really produced and really published, and a second copy of that
walk would eventually disagree with the first.
"""

from __future__ import annotations

# The ``world`` fixture is a module-level name and every test takes a parameter
# of the same name; ruff reads that as a redefinition on every signature. See
# ``test_pr_derivatives_and_publications`` for the same note.
# ruff: noqa: F811
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from meobot.application.pr_publication_service import (
    UpdatePublicationCommand,
)
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.pr_reporting import PrPostMetricSnapshot, PrPublication
from meobot.db.models.pr_transition import PrContentTransitionEvent
from meobot.domain.pr.errors import (
    PrConflictError,
    PrPermissionDeniedError,
    PrPublishedContentError,
    PrValidationError,
    PrWorkflowTransitionError,
)
from meobot.domain.pr.models import PrWorkflowStage
from meobot.domain.pr.reporting import (
    ACTIVE_PUBLICATION_STATUSES,
    PrMetricSource,
    PrPublicationStatus,
    is_active_publication,
)
from meobot.domain.pr.workflow import PrTransitionTrigger
from tests.unit.test_pr_derivatives_and_publications import (
    POST_URL,
    add_derivative,
    force_stage,
    new_channel,
    publish,
    ready_to_publish,
    stage_of,
)
from tests.unit.test_pr_production_lifecycle import (  # noqa: F401 - `world` is a fixture
    NOW,
    World,
    audit_actions,
    world,
)

pytestmark = pytest.mark.asyncio

FIXED_URL = "https://www.tiktok.com/@apexmed/video/7311111111111111111"


def _instant(moment: datetime) -> datetime:
    """The same instant, tagged UTC.

    SQLite has no timezone type and hands a ``DateTime(timezone=True)`` column
    back naive, so a value written as UTC comes out unlabelled. Every timestamp
    in this file is UTC on both sides; this is what lets a test compare the
    instant rather than the tagging.
    """
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


# --- Helpers ----------------------------------------------------------------


async def publications_of(world: World, content_id: uuid.UUID) -> list[PrPublication]:
    rows = await world.session.execute(
        select(PrPublication)
        .where(PrPublication.content_id == content_id)
        .order_by(PrPublication.code.asc())
    )
    return list(rows.scalars().all())


async def add_metric(world: World, publication: PrPublication) -> None:
    """One observation against a real post, which is what makes it unsafe to unsay."""
    world.session.add(
        PrPostMetricSnapshot(
            publication_id=publication.id,
            observed_at=NOW + timedelta(days=1),
            source=PrMetricSource.MANUAL,
            views=1200,
        )
    )
    await world.session.flush()


async def reverse(world: World, publication: PrPublication, *, actor=None):  # type: ignore[no-untyped-def]
    return await world.services.publications.reverse_publication(
        actor=world.actor(actor or world.owner),
        request_id=world.request_id,
        publication_id=publication.id,
    )


async def edit(world: World, publication: PrPublication, *, actor=None, **fields):  # type: ignore[no-untyped-def]
    return await world.services.publications.update_publication(
        actor=world.actor(actor or world.owner),
        request_id=world.request_id,
        command=UpdatePublicationCommand(publication_id=publication.id, **fields),
    )


async def transitions(world: World, content_id: uuid.UUID) -> list[PrContentTransitionEvent]:
    rows = await world.session.execute(
        select(PrContentTransitionEvent)
        .where(PrContentTransitionEvent.content_id == content_id)
        .order_by(PrContentTransitionEvent.created_at.asc())
    )
    return list(rows.scalars().all())


# ===========================================================================
# 1-2: ONE PUBLISH ACTION
# ===========================================================================


async def test_01_marking_published_by_naming_the_stage_is_refused(world: World) -> None:
    """Requirement 1. The standalone transition is gone, at the source.

    Not merely hidden: the edge no longer exists for a ``MANUAL`` driver, so the
    generic transition route, the Telegram tool and any script are refused
    together. Before this, a piece could reach ``PUBLISHED`` with an empty
    publication history - which is the record this whole feature exists to stop
    producing.
    """
    content_id, _ = await ready_to_publish(world)

    with pytest.raises(PrWorkflowTransitionError) as raised:
        await world.services.workflow.request_transition(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            content_id=content_id,
            target=PrWorkflowStage.PUBLISHED,
        )
    assert PrWorkflowStage.PUBLISHED.value not in raised.value.details["allowed"]
    assert await stage_of(world, content_id) is PrWorkflowStage.READY_TO_PUBLISH
    assert await publications_of(world, content_id) == []


async def test_01a_the_action_list_offers_no_publish_transition(world: World) -> None:
    """Requirement 1, as the panel sees it.

    The button disappears because the **server stopped offering it**, not
    because a client stopped drawing it. What is offered instead is
    ``RECORD_PUBLICATION``: one business operation, not two.
    """
    content_id, _ = await ready_to_publish(world)
    content = await world.reload(content_id)
    offered = await world.services.actions.for_content(
        actor=world.actor(world.owner), content=content
    )
    targets = {action.target_stage for action in offered if action.target_stage is not None}
    assert PrWorkflowStage.PUBLISHED not in targets
    assert "RECORD_PUBLICATION" in {action.kind.value for action in offered}


async def test_02_the_first_publication_still_publishes_the_content(world: World) -> None:
    """Requirement 2. The one path still works, and still moves the stage.

    Publication row and stage change in one commit, with the structured history
    Step 1F.2.3b added carrying the new ``PUBLICATION`` trigger - which is what a
    reversal later reads to know this event is the one that published the piece.
    """
    content_id, master = await ready_to_publish(world)
    publication = await publish(world, content_id, submission=master)

    assert await stage_of(world, content_id) is PrWorkflowStage.PUBLISHED
    published = [
        event
        for event in await transitions(world, content_id)
        if event.to_stage is PrWorkflowStage.PUBLISHED
    ]
    assert len(published) == 1
    assert published[0].trigger is PrTransitionTrigger.PUBLICATION
    assert published[0].note == f"publication:{publication.code}"


# ===========================================================================
# 3-10: CORRECTING A PUBLICATION
# ===========================================================================


async def test_03_05_the_url_the_time_and_the_note_are_correctable(world: World) -> None:
    """Requirements 3, 4 and 5. Everything that is a transcription of the event."""
    content_id, master = await ready_to_publish(world)
    publication = await publish(world, content_id, submission=master, note="bản đầu")
    later = datetime(2026, 10, 18, 20, 10, tzinfo=UTC)

    fixed = await edit(
        world, publication, url=FIXED_URL, published_at=later, note="sửa sau khi đổi tên kênh"
    )
    assert fixed.url == FIXED_URL
    # SQLite hands timestamps back without a zone; the instant is what is being
    # asserted, so it is compared as one rather than as a tagged value.
    assert _instant(fixed.published_at) == later
    assert fixed.note == "sửa sau khi đổi tên kênh"
    # And nothing about what the row means moved.
    assert fixed.channel_id == world.channel_id
    assert fixed.production_submission_id == master.id
    assert await stage_of(world, content_id) is PrWorkflowStage.PUBLISHED


async def test_06_07_the_channel_and_the_output_are_not_correctable(world: World) -> None:
    """Requirements 6 and 7. Unrepresentable rather than merely refused.

    ``UpdatePublicationCommand`` has no field for either, and the request model
    has none either, so there is no way to ask - which is a stronger guarantee
    than a check somebody could forget to write. A row entered against the wrong
    channel is reversed and re-entered, and both facts stay visible.
    """
    from meobot.api.schemas.pr import UpdatePublicationRequest

    forbidden = {"channel_id", "production_submission_id", "derivative_id"}
    assert forbidden.isdisjoint(UpdatePublicationCommand.__dataclass_fields__)
    assert forbidden.isdisjoint(UpdatePublicationRequest.model_fields)

    # And the route refuses to carry them rather than ignoring them, because the
    # request model forbids extras.
    content_id, master = await ready_to_publish(world)
    publication = await publish(world, content_id, submission=master)
    other = await new_channel(world)
    world.act_as(world.owner)
    response = world.client.patch(
        f"/api/pr/contents/{content_id}/publications/{publication.id}",
        json={"channel_id": str(other.id)},
    )
    assert response.status_code == 422, response.text
    await world.session.refresh(publication)
    assert publication.channel_id == world.channel_id


async def test_08_a_correction_is_audited(world: World) -> None:
    """Requirement 8. What changed, from what to what, and by whom.

    Only the fields that moved appear on either side, so a reader sees the
    change rather than a copy of the row - and the note's *value* is carried
    because it is a short human field, not a content body.
    """
    content_id, master = await ready_to_publish(world)
    publication = await publish(world, content_id, submission=master)
    original_url = publication.url

    await edit(world, publication, url=FIXED_URL, note="đổi link")

    rows = await world.session.execute(
        select(AuditLog)
        .where(AuditLog.entity_id == str(publication.id))
        .order_by(AuditLog.created_at.asc())
    )
    entry = next(row for row in rows.scalars().all() if row.action == "pr.publication.updated")
    assert entry.before_data["url"] == original_url
    assert entry.after_data["url"] == FIXED_URL
    assert entry.after_data["note"] == "đổi link"
    assert entry.after_data["content_code"]
    # Untouched fields are on neither side.
    assert "published_at" not in entry.before_data
    assert "published_at" not in entry.after_data


async def test_08a_an_unedited_form_is_not_an_event(world: World) -> None:
    """A no-op correction writes no audit row - the rule its neighbours follow."""
    content_id, master = await ready_to_publish(world)
    publication = await publish(world, content_id, submission=master)

    await edit(world, publication, url=publication.url, note=publication.note or "")
    assert "pr.publication.updated" not in await audit_actions(world, publication.id)


async def test_09_an_unusable_edited_url_is_refused(world: World) -> None:
    """Requirement 9. The same validator as creation, not a second copy.

    A scheme this product refuses on the way in must not become storable on the
    way through a correction - which is exactly the hole a duplicated validator
    eventually opens.
    """
    content_id, master = await ready_to_publish(world)
    publication = await publish(world, content_id, submission=master)

    for bad in ("javascript:alert(1)", "data:text/html,<script>", "tiktok.com/@a/video/1"):
        with pytest.raises(PrValidationError):
            await edit(world, publication, url=bad)
    await world.session.refresh(publication)
    assert publication.url == POST_URL


async def test_10_an_unrelated_user_can_neither_edit_nor_reverse(world: World) -> None:
    """Requirements 10 and 28. Enforced in the service, not by hiding a button.

    The rule is ``PR_PUBLICATION_REGISTER`` - the capability that has always
    guarded "this went public". The Owner holds it, an ``ADMIN`` holds it, and a
    member who may read the whole piece does not: being able to see a publication
    is not being able to rewrite one.

    Checked over the router as well, because "a direct API call cannot bypass
    this" is only meaningful there.
    """
    content_id, master = await ready_to_publish(world)
    publication = await publish(world, content_id, submission=master)

    for actor in (world.member, world.other, world.lead):
        with pytest.raises(PrPermissionDeniedError):
            await edit(world, publication, url=FIXED_URL, actor=actor)
        with pytest.raises(PrPermissionDeniedError):
            await reverse(world, publication, actor=actor)

    world.act_as(world.member)
    assert (
        world.client.patch(
            f"/api/pr/contents/{content_id}/publications/{publication.id}",
            json={"url": FIXED_URL},
        ).status_code
        == 403
    )
    assert (
        world.client.post(
            f"/api/pr/contents/{content_id}/publications/{publication.id}/reverse"
        ).status_code
        == 403
    )
    await world.session.refresh(publication)
    assert publication.url == POST_URL
    assert publication.status is PrPublicationStatus.PUBLISHED


async def test_10a_the_owner_may_do_both(world: World) -> None:
    """Requirement 28, the positive half, and the offers that match it.

    The Owner holds every permission and therefore this capability; the offers
    are computed from the same predicate the writes require, so a control never
    appears where the write would refuse.
    """
    content_id, master = await ready_to_publish(world)
    publication = await publish(world, content_id, submission=master)

    assert (await edit(world, publication, url=FIXED_URL)).url == FIXED_URL
    offered = await world.actions(world.owner, content_id)
    assert {"EDIT_ANY_PUBLICATION", "REVERSE_PUBLICATION"} <= offered
    assert not {"EDIT_ANY_PUBLICATION", "REVERSE_PUBLICATION"} & await world.actions(
        world.member, content_id
    )

    outcome = await reverse(world, publication)
    assert outcome.publication.status is PrPublicationStatus.REVERSED


# ===========================================================================
# 11-17: REVERSING A PUBLICATION
# ===========================================================================


async def test_11_13_a_reversal_marks_the_row_and_keeps_it(world: World) -> None:
    """Requirements 11, 12 and 13. A correction, not an erasure.

    The row is still there with its channel, its output, its URL and its
    instant - publication history is operational evidence - and it stops
    counting, which is what ``is_active_publication`` is for.
    """
    content_id, master = await ready_to_publish(world)
    publication = await publish(world, content_id, submission=master, note="nhầm kênh")

    outcome = await reverse(world, publication)
    assert outcome.stage_reverted is True

    rows = await publications_of(world, content_id)
    assert len(rows) == 1
    kept = rows[0]
    assert kept.id == publication.id
    assert kept.status is PrPublicationStatus.REVERSED
    assert is_active_publication(kept.status) is False
    # Everything about what happened is preserved.
    assert kept.channel_id == world.channel_id
    assert kept.production_submission_id == master.id
    assert kept.url == POST_URL
    assert kept.note == "nhầm kênh"


async def test_13a_active_is_one_predicate_and_removed_still_counts(world: World) -> None:
    """Requirement 13's other half: what "active" means, decided once.

    ``REMOVED`` and ``UNAVAILABLE`` are **active**. A post that was taken down
    still went out - people saw it - so a piece whose only publication was
    removed must not become *Sẵn sàng đăng* again. Only ``REVERSED`` says the
    posting never happened.
    """
    assert (
        frozenset(
            {
                PrPublicationStatus.PUBLISHED,
                PrPublicationStatus.REMOVED,
                PrPublicationStatus.UNAVAILABLE,
            }
        )
        == ACTIVE_PUBLICATION_STATUSES
    )
    assert is_active_publication(PrPublicationStatus.REVERSED) is False

    content_id, master = await ready_to_publish(world)
    publication = await publish(world, content_id, submission=master)
    publication.status = PrPublicationStatus.REMOVED
    await world.session.flush()

    # Taken down, not un-said: another publication may still be recorded, and
    # nothing about the stage moved.
    assert await stage_of(world, content_id) is PrWorkflowStage.PUBLISHED


async def test_14_the_reversal_is_audited(world: World) -> None:
    """Requirement 14. Including whether the stage went back with it."""
    content_id, master = await ready_to_publish(world)
    publication = await publish(world, content_id, submission=master)

    await reverse(world, publication)

    rows = await world.session.execute(
        select(AuditLog).where(AuditLog.entity_id == str(publication.id))
    )
    entry = next(row for row in rows.scalars().all() if row.action == "pr.publication.reversed")
    assert entry.before_data["status"] == PrPublicationStatus.PUBLISHED.value
    assert entry.after_data["status"] == PrPublicationStatus.REVERSED.value
    assert entry.after_data["stage_reverted"] is True
    assert entry.after_data["workflow_stage"] == PrWorkflowStage.READY_TO_PUBLISH.value
    assert entry.after_data["content_code"]


async def test_15_18_the_first_publication_undone_returns_the_content(world: World) -> None:
    """Requirements 15 and 18. The scenario the step was written around.

    Owner records the first publication, realises it was wrong, and takes it
    back. One publication, no metrics, stage ``PUBLISHED``: the piece goes back
    to *Sẵn sàng đăng*, and the compensating move is a **linked pair** in the
    append-only history rather than an edit of the row that published it.
    """
    content_id, master = await ready_to_publish(world)
    publication = await publish(world, content_id, submission=master)
    forward = next(
        event
        for event in await transitions(world, content_id)
        if event.trigger is PrTransitionTrigger.PUBLICATION
    )

    outcome = await reverse(world, publication)

    assert outcome.stage_reverted is True
    assert outcome.reason is None
    assert await stage_of(world, content_id) is PrWorkflowStage.READY_TO_PUBLISH

    await world.session.refresh(forward)
    assert forward.reversed_by_event_id is not None
    undo = [
        event
        for event in await transitions(world, content_id)
        if event.trigger is PrTransitionTrigger.UNDO
    ][-1]
    assert undo.from_stage is PrWorkflowStage.PUBLISHED
    assert undo.to_stage is PrWorkflowStage.READY_TO_PUBLISH
    assert undo.reverses_event_id == forward.id
    assert undo.note == f"publication_reversed:{publication.code}"
    # Nothing was rewritten: the forward row still says what it said.
    assert forward.to_stage is PrWorkflowStage.PUBLISHED


async def test_16_19_reversing_one_of_two_leaves_the_content_published(world: World) -> None:
    """Requirements 16 and 19. Facebook stays up, so the piece is still published."""
    content_id, master = await ready_to_publish(world)
    facebook = await publish(world, content_id, submission=master)
    derivative = await add_derivative(world, content_id)
    tiktok_channel = await new_channel(world)
    tiktok = await publish(
        world,
        content_id,
        channel_id=tiktok_channel.id,
        derivative=derivative,
        at=NOW + timedelta(days=1),
    )

    outcome = await reverse(world, tiktok)

    assert outcome.stage_reverted is False
    assert outcome.reason == "other_active_publications"
    assert await stage_of(world, content_id) is PrWorkflowStage.PUBLISHED
    statuses = {row.id: row.status for row in await publications_of(world, content_id)}
    assert statuses[tiktok.id] is PrPublicationStatus.REVERSED
    assert statuses[facebook.id] is PrPublicationStatus.PUBLISHED
    # And no undo transition was written for a stage that did not move.
    assert not [
        event
        for event in await transitions(world, content_id)
        if event.trigger is PrTransitionTrigger.UNDO
    ]


async def test_17_20_reversing_the_last_one_needs_the_history_to_agree(world: World) -> None:
    """Requirements 17 and 20. The count is not the only condition.

    Both publications are reversed, one after the other. The second reversal
    leaves nothing active *and* finds the ``PUBLICATION`` transition still in
    force, so the piece goes back - which is what "do not rely only on the
    current count" means in practice: the history is what says which event put it
    here and that nobody has taken it back already.
    """
    content_id, master = await ready_to_publish(world)
    first = await publish(world, content_id, submission=master)
    second_channel = await new_channel(world)
    second = await publish(
        world,
        content_id,
        channel_id=second_channel.id,
        submission=master,
        at=NOW + timedelta(days=1),
    )

    assert (await reverse(world, first)).stage_reverted is False
    assert await stage_of(world, content_id) is PrWorkflowStage.PUBLISHED

    outcome = await reverse(world, second)
    assert outcome.stage_reverted is True
    assert await stage_of(world, content_id) is PrWorkflowStage.READY_TO_PUBLISH
    assert all(
        row.status is PrPublicationStatus.REVERSED
        for row in await publications_of(world, content_id)
    )


async def test_17a_reversing_twice_is_refused(world: World) -> None:
    """A reversed row is history. Pressing the button again is a 409, not a no-op."""
    content_id, master = await ready_to_publish(world)
    publication = await publish(world, content_id, submission=master)
    await reverse(world, publication)

    with pytest.raises(PrConflictError) as raised:
        await reverse(world, publication)
    assert raised.value.details["reason"] == "already_reversed"

    with pytest.raises(PrConflictError) as edited:
        await edit(world, publication, url=FIXED_URL)
    assert edited.value.details["reason"] == "already_reversed"


async def test_18a_a_reversed_piece_can_be_published_again(world: World) -> None:
    """The end state is usable: back at *Sẵn sàng đăng*, and publishable.

    The second publication finds the first ``PUBLICATION`` transition already
    reversed and writes its own, so a piece corrected and re-published has an
    honest history of both attempts rather than one overwritten one.
    """
    content_id, master = await ready_to_publish(world)
    first = await publish(world, content_id, submission=master)
    await reverse(world, first)

    right_channel = await new_channel(world)
    second = await publish(
        world,
        content_id,
        channel_id=right_channel.id,
        submission=master,
        at=NOW + timedelta(days=2),
    )
    assert await stage_of(world, content_id) is PrWorkflowStage.PUBLISHED
    assert second.status is PrPublicationStatus.PUBLISHED
    forward = [
        event
        for event in await transitions(world, content_id)
        if event.trigger is PrTransitionTrigger.PUBLICATION
    ]
    assert len(forward) == 2


# ===========================================================================
# 18-20: WHAT MAKES A REVERSAL UNSAFE
# ===========================================================================


async def test_18b_metrics_on_this_publication_refuse_the_reversal(world: World) -> None:
    """Requirement 18. Numbers were read off a real post.

    Reversing the row they hang from would leave observations claiming views for
    a posting the record says never took place, so the reversal itself is refused
    rather than the stage move alone.
    """
    content_id, master = await ready_to_publish(world)
    publication = await publish(world, content_id, submission=master)
    await add_metric(world, publication)

    with pytest.raises(PrConflictError) as raised:
        await reverse(world, publication)
    assert raised.value.details["reason"] == "has_metrics"
    await world.session.refresh(publication)
    assert publication.status is PrPublicationStatus.PUBLISHED
    assert await stage_of(world, content_id) is PrWorkflowStage.PUBLISHED


async def test_18c_metrics_elsewhere_stop_the_stage_from_moving(world: World) -> None:
    """Requirement 18, the softer half.

    The publication being reversed has no numbers of its own, but another
    publication of the same content does. The row is taken back - it is
    genuinely wrong - and the content stays ``PUBLISHED``, because un-publishing
    it would leave a report describing a piece the record says never went out.
    """
    content_id, master = await ready_to_publish(world)
    measured = await publish(world, content_id, submission=master)
    await add_metric(world, measured)
    second_channel = await new_channel(world)
    mistaken = await publish(
        world,
        content_id,
        channel_id=second_channel.id,
        submission=master,
        at=NOW + timedelta(days=1),
    )
    # Take the measured one out of the "active" set the honest way, so the only
    # thing left blocking the stage move is the metric.
    measured.status = PrPublicationStatus.REVERSED
    await world.session.flush()

    outcome = await reverse(world, mistaken)
    assert outcome.stage_reverted is False
    assert outcome.reason == "has_metrics"
    assert await stage_of(world, content_id) is PrWorkflowStage.PUBLISHED


async def test_19_content_at_a_non_publishable_stage_never_moves_backwards(
    world: World,
) -> None:
    """Requirement 19, through a stage that cannot take a publication back.

    Correcting a mistyped link is exactly the correction somebody needs to make
    and it changes nothing underneath - so **edit is allowed**. Reversing the
    publication is not, and is refused with a reason rather than silently doing
    half of it.

    ``MEASURED`` is the stage used here, and Step 1F.2.3f.5 retired it. The test
    is kept and forces the stage directly on purpose: a legacy row could still
    carry it, and *"a stage outside ``PUBLISHABLE_STAGES`` refuses a reversal"*
    is the rule, not something about measuring.
    """
    content_id, master = await ready_to_publish(world)
    publication = await publish(world, content_id, submission=master)
    await force_stage(world, content_id, PrWorkflowStage.MEASURED)

    assert (await edit(world, publication, url=FIXED_URL)).url == FIXED_URL

    with pytest.raises(PrConflictError) as raised:
        await reverse(world, publication)
    assert raised.value.details["reason"] == "stage_not_publishable_back"
    assert await stage_of(world, content_id) is PrWorkflowStage.MEASURED
    await world.session.refresh(publication)
    assert publication.status is PrPublicationStatus.PUBLISHED
    # And the offer matches: correcting yes, reversing no.
    offered = await world.actions(world.owner, content_id)
    assert "EDIT_ANY_PUBLICATION" in offered
    assert "REVERSE_PUBLICATION" not in offered


async def test_20_archived_publication_history_is_read_only(world: World) -> None:
    """Requirement 20. ``ARCHIVED`` keeps the meaning it already had.

    Nothing transitions out of it, nothing deletes it, and its publication
    history is part of what was put away. Both operations refuse, and neither
    control is offered.
    """
    content_id, master = await ready_to_publish(world)
    publication = await publish(world, content_id, submission=master)
    await force_stage(world, content_id, PrWorkflowStage.ARCHIVED)

    with pytest.raises(PrConflictError) as edited:
        await edit(world, publication, url=FIXED_URL)
    assert edited.value.details["reason"] == "archived"

    with pytest.raises(PrConflictError) as reversed_:
        await reverse(world, publication)
    assert reversed_.value.details["reason"] == "stage_not_publishable_back"

    offered = await world.actions(world.owner, content_id)
    assert not {"EDIT_ANY_PUBLICATION", "REVERSE_PUBLICATION", "RECORD_PUBLICATION"} & offered
    await world.session.refresh(publication)
    assert publication.url == POST_URL
    assert publication.status is PrPublicationStatus.PUBLISHED


# ===========================================================================
# 21-24: WHAT A REVERSAL MUST NOT BUY
# ===========================================================================


async def test_21_a_reversed_publication_still_blocks_hard_delete(world: World) -> None:
    """Requirement 21. Undo is not a way to earn the right to destroy evidence.

    The content is back at ``READY_TO_PUBLISH``, which is below the delete floor,
    and it is *still* undeletable - because the rule counts **any** publication
    row, reversed included. A piece that has been through the act of being
    published has a history that must survive.
    """
    content_id, master = await ready_to_publish(world)
    publication = await publish(world, content_id, submission=master)
    await reverse(world, publication)
    assert await stage_of(world, content_id) is PrWorkflowStage.READY_TO_PUBLISH

    with pytest.raises(PrPublishedContentError) as raised:
        await world.services.lifecycle.delete_content(
            actor=world.actor(world.owner), request_id=world.request_id, content_id=content_id
        )
    assert raised.value.details["reason"]
    assert len(await publications_of(world, content_id)) == 1


async def test_22_a_historically_published_derivative_stays_protected(world: World) -> None:
    """Requirement 22. A reversed publication is still evidence of a reference.

    The derivative was published once; the record of that posting says so even
    now that it has been taken back. Deleting the file underneath it would leave
    the publication naming something that no longer exists, so the refusal is
    unchanged - the check counts every publication, not only the active ones.
    """
    content_id, _ = await ready_to_publish(world)
    derivative = await add_derivative(world, content_id)
    publication = await publish(world, content_id, derivative=derivative)
    await reverse(world, publication)

    with pytest.raises(PrConflictError) as raised:
        await world.services.content_assets.delete_derivative(
            actor=world.actor(world.member),
            request_id=world.request_id,
            derivative_id=derivative.id,
        )
    assert raised.value.details["reason"] == "published_output_is_immutable"


async def test_23_a_refused_correction_changes_nothing(world: World) -> None:
    """Requirement 23. Validate, then write - so a refusal leaves no half-edit.

    The URL is the last thing validated, and a request that changes the note and
    the instant as well leaves all three where they were.
    """
    content_id, master = await ready_to_publish(world)
    publication = await publish(world, content_id, submission=master, note="gốc")
    before = (publication.url, _instant(publication.published_at), publication.note)

    with pytest.raises(PrValidationError):
        await edit(
            world,
            publication,
            url="javascript:alert(1)",
            published_at=NOW + timedelta(days=5),
            note="đã sửa",
        )

    await world.session.refresh(publication)
    assert (publication.url, _instant(publication.published_at), publication.note) == before
    assert "pr.publication.updated" not in await audit_actions(world, publication.id)


async def test_24_a_refused_reversal_changes_nothing(world: World) -> None:
    """Requirement 24. No half status change, no half stage move, no half audit."""
    content_id, master = await ready_to_publish(world)
    publication = await publish(world, content_id, submission=master)
    await add_metric(world, publication)
    before = await transitions(world, content_id)

    with pytest.raises(PrConflictError):
        await reverse(world, publication)

    await world.session.refresh(publication)
    assert publication.status is PrPublicationStatus.PUBLISHED
    assert await stage_of(world, content_id) is PrWorkflowStage.PUBLISHED
    assert len(await transitions(world, content_id)) == len(before)
    assert "pr.publication.reversed" not in await audit_actions(world, publication.id)


async def test_24a_no_notification_is_raised_for_a_correction_or_a_reversal(
    world: World,
) -> None:
    """Requirement 37 of the step. Audit and history are enough.

    Correcting a link is not news for anybody else, and a reversal is a
    correction. The record carries both.
    """
    from meobot.db.models.user_notification import UserNotification

    content_id, master = await ready_to_publish(world)
    publication = await publish(world, content_id, submission=master)

    async def inbox() -> int:
        found = await world.session.execute(select(UserNotification.id))
        return len(list(found.scalars().all()))

    before = await inbox()
    await edit(world, publication, url=FIXED_URL)
    await reverse(world, publication)
    assert await inbox() == before


async def test_24b_the_generic_undo_still_refuses_to_un_publish(world: World) -> None:
    """Requirement 22 of the step. Publication reversal is not the generic undo.

    Step 1F.2.3b's undo classifies only ``HUMAN_APPROVAL`` edges, so a
    ``PUBLICATION`` transition has never been a candidate and still is not -
    which is what keeps "hoàn tác" on the detail header meaning "take back the
    decision I just made" rather than "un-publish this".
    """
    content_id, master = await ready_to_publish(world)
    await publish(world, content_id, submission=master)
    content = await world.reload(content_id)

    assert await world.services.undo.candidate(content) is None
    assert "UNDO_LAST_ACTION" not in await world.actions(world.owner, content_id)
