"""Step 1F.2.3f.2 - where a file may live, and who may say it went out.

Numbered 1-45, following the requirement numbering the step was specified with:
1-12 asset locations, 13-20 correcting a handed-in file, 21-31 contributor
publishing, 32-40 correcting a publication, 41-45 reversal.

Two corrections, and they come from the same complaint: **the system was refusing
things people actually do.**

* a producer pasting ``M:\\XAY KENH\\video.mp4`` - the mapped drive their NAS
  share appears as - was told the field only takes ``http://`` or ``https://``,
  so the value that eventually got stored was less accurate than the one they
  started with. A stricter validator produced worse data;
* the person who had just posted the video could not write down that they had,
  because recording a publication needed ``publish.social`` and that is
  ``ADMIN``-and-above. Somebody with the permission had to be found and told,
  which is how a daily task becomes a bottleneck and how a publication record
  ends up attributed to the wrong person.

The world and the helpers are Step 1F.2.3f's and f.1's, for the reason those
files already give: everything here happens to a piece that was really produced
and really published.
"""

from __future__ import annotations

# The ``world`` fixture is a module-level name and every test takes a parameter
# of the same name; ruff reads that as a redefinition on every signature.
# ruff: noqa: F811
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from meobot.application.pr_channel_service import CreateChannelCommand
from meobot.application.pr_production_service import CorrectSubmissionCommand
from meobot.application.pr_publication_service import RegisterPublicationCommand
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.pr import PrChannel, PrContentItem, PrPlatform
from meobot.db.models.pr_production import PrProductionSubmission
from meobot.domain.identity.models import Role
from meobot.domain.pr.assets import is_link_location, normalize_asset_location
from meobot.domain.pr.errors import (
    PrConflictError,
    PrPermissionDeniedError,
    PrValidationError,
)
from meobot.domain.pr.models import (
    PrChannelAssignmentRole,
    PrChannelCategory,
    PrProductionArtifactType,
    PrWorkflowStage,
)
from meobot.domain.pr.policy import PrCapability, meets_baseline
from meobot.domain.pr.production import looks_like_storage_path, normalize_artifact
from meobot.domain.pr.reporting import PrPublicationStatus
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

#: The four storage shapes the team actually types. The mapped drive has spaces
#: in it because the folders do.
POSIX_PATH = "/volume1/media/2026/video-final.mp4"
UNC_PATH = "\\\\NAS\\share\\campaign\\video.mp4"
WINDOWS_PATH = "M:\\XAY KENH\\video-final.mp4"
RELATIVE_PATH = "shared/campaign/video-final.mp4"

DRIVE_URL = "https://drive.google.com/file/d/1MasterSixty/view"
PLAIN_HTTPS = "https://cdn.example.test/video-final.mp4"
PLAIN_HTTP = "http://cdn.example.test/video-final.mp4"

#: Every shape a stored location may take, with the artifact type a producer
#: would choose for it.
ACCEPTED: tuple[tuple[str, PrProductionArtifactType], ...] = (
    (PLAIN_HTTPS, PrProductionArtifactType.EXTERNAL_LINK),
    (PLAIN_HTTP, PrProductionArtifactType.EXTERNAL_LINK),
    (DRIVE_URL, PrProductionArtifactType.DRIVE_LINK),
    (POSIX_PATH, PrProductionArtifactType.NAS_PATH),
    (UNC_PATH, PrProductionArtifactType.NAS_PATH),
    (WINDOWS_PATH, PrProductionArtifactType.NAS_PATH),
    (RELATIVE_PATH, PrProductionArtifactType.NAS_PATH),
)

#: Schemes that are script execution wearing a link's clothes. Refused wherever a
#: location is stored, and never rendered as an anchor.
UNSAFE = ("javascript:alert(1)", "data:text/html,<script>x</script>", "vbscript:msgbox")


async def correct(world: World, submission: PrProductionSubmission, *, actor=None, **fields):  # type: ignore[no-untyped-def]
    return await world.services.production.correct_submission(
        actor=world.actor(actor or world.member),
        request_id=world.request_id,
        command=CorrectSubmissionCommand(submission_id=submission.id, **fields),
    )


async def assign_channel(world: World, channel: PrChannel, user) -> None:  # type: ignore[no-untyped-def]
    """Put somebody on a channel, through the service that owns the table."""
    await world.services.channels.assign_user(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        channel_id=channel.id,
        user_id=user.id,
        assignment_role=PrChannelAssignmentRole.CHANNEL_OWNER,
        effective_from=date(2026, 1, 1),
    )


async def unplanned_channel(world: World, name: str = "Kênh của người khác") -> PrChannel:
    """A channel this content does not target and nobody in the test operates."""
    platform = PrPlatform(code=f"PLT{uuid.uuid4().hex[:6].upper()}", name="Kênh lạ")
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


# ===========================================================================
# 1-12: WHAT AN ASSET LOCATION MAY BE
# ===========================================================================


@pytest.mark.parametrize(("location", "artifact_type"), ACCEPTED)
async def test_01_07_every_real_storage_shape_is_accepted(
    world: World, location: str, artifact_type: PrProductionArtifactType
) -> None:
    """Requirements 1-7. All seven, stored exactly as typed.

    The two that are new are the ones the complaint was about: a mapped drive
    (``M:\\…``, spaces and all) and a location relative to a root the team
    already shares. Both were refused before this step, and refusing them meant
    somebody retyped a path into a shape the form would take - which is how a
    validator makes the stored data worse rather than safer.
    """
    assert normalize_artifact(artifact_type, location) == location
    # And through the shape-inferring entry point, which the derivative service
    # uses because it has no ``artifact_type`` column to ask.
    assert normalize_asset_location(location) == location


async def test_08_09_unsafe_schemes_are_refused_everywhere(world: World) -> None:
    """Requirements 8 and 9. Script execution wearing a link's clothes.

    Refused by every path into a stored location - the type-aware one, the
    shape-inferring one, and the publication URL - because a stored location is
    rendered as a link and the accepted scheme set is therefore a security
    boundary rather than a formatting preference.
    """
    from meobot.domain.pr.assets import normalize_destination_url, normalize_publication_url

    for bad in UNSAFE:
        for call in (
            lambda value: normalize_artifact(PrProductionArtifactType.EXTERNAL_LINK, value),
            lambda value: normalize_artifact(PrProductionArtifactType.NAS_PATH, value),
            normalize_asset_location,
            normalize_publication_url,
            normalize_destination_url,
        ):
            with pytest.raises(PrValidationError):
                call(bad)


async def test_09a_an_unsafe_scheme_is_never_a_link(world: World) -> None:
    """Requirement 69 of the step: no unsafe value can be made clickable.

    Belt and braces. Nothing unsafe reaches a stored row, but the *display* rule
    is positive - "names a scheme we allow" - rather than "is not a path", so it
    would refuse to linkify one even if a row somehow held it.
    """
    for bad in UNSAFE:
        assert is_link_location(bad) is False
    for path in (POSIX_PATH, UNC_PATH, WINDOWS_PATH, RELATIVE_PATH):
        assert is_link_location(path) is False, path
        assert looks_like_storage_path(path) is True, path
    for url in (PLAIN_HTTP, PLAIN_HTTPS, DRIVE_URL):
        assert is_link_location(url) is True, url
        assert looks_like_storage_path(url) is False, url


async def test_10_nothing_reaches_the_network(world: World) -> None:
    """Requirement 10. A validator that made a request would be an SSRF surface.

    Asserted structurally, over the whole domain package: no HTTP client is
    imported anywhere near these rules, so there is nothing to accidentally
    call. The service-level proof is that every test in this file validates a
    host that does not exist and none of them is slow.
    """
    import pathlib
    import re

    forbidden = re.compile(r"^\s*(?:import|from)\s+(httpx|requests|aiohttp|urllib\.request)\b")
    domain = pathlib.Path("src/meobot/domain/pr")
    offenders = [
        str(path)
        for path in domain.rglob("*.py")
        if forbidden.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], offenders
    # A host nobody can resolve validates instantly, because nothing looks it up.
    assert normalize_asset_location("https://nowhere.invalid/a.mp4")


async def test_11_a_derivative_uses_the_same_validator(world: World) -> None:
    """Requirement 11. One rule, two callers.

    The derivative service has no ``artifact_type`` column, so it infers the
    shape - but the rule it infers *into* is the one a production submission
    goes through, which is why a path acceptable on a master is acceptable on a
    cutdown.
    """
    content_id, _ = await ready_to_publish(world)
    for location in (WINDOWS_PATH, RELATIVE_PATH, UNC_PATH, DRIVE_URL):
        derivative = await add_derivative(world, content_id, location=location)
        assert derivative.location == location


async def test_12_a_publication_url_is_still_a_web_url(world: World) -> None:
    """Requirement 12, and requirement 39 of the step. **Not** weakened.

    An asset location says where a file is kept; a publication URL says where
    the public post is. Accepting a NAS path as "link bài đăng" would store
    something no reader could open, so every path shape is refused there - by
    the same function, asked a different question.
    """
    content_id, master = await ready_to_publish(world)
    for path in (POSIX_PATH, UNC_PATH, WINDOWS_PATH, RELATIVE_PATH):
        with pytest.raises(PrValidationError) as raised:
            await publish(world, content_id, submission=master, url=path)
        assert raised.value.details["reason"] in {"missing_scheme", "unsupported_scheme"}
    assert await world.services.publications.list_publications(content_id) == []

    # And a real post URL still works.
    assert (await publish(world, content_id, submission=master, url=POST_URL)).url == POST_URL


# ===========================================================================
# 13-20: CORRECTING A HANDED-IN FILE
# ===========================================================================


async def test_13_the_submitter_may_correct_their_own_output(world: World) -> None:
    """Requirement 13. *"Người submit link sản phẩm có quyền edit link."*

    The member handed the file in, so the member fixes the path. Nothing else
    about the row moves - and there is no field to move it with.
    """
    _, master = await ready_to_publish(world)
    assert master.submitted_by_user_id == world.member.id

    fixed = await correct(
        world,
        master,
        artifact_type=PrProductionArtifactType.NAS_PATH,
        location=WINDOWS_PATH,
        note="Đã chuyển sang ổ M",
    )
    assert fixed.location == WINDOWS_PATH
    assert fixed.artifact_type is PrProductionArtifactType.NAS_PATH
    assert fixed.note == "Đã chuyển sang ổ M"


async def test_14_an_unrelated_member_may_not(world: World) -> None:
    """Requirement 14, and requirement 11 of the step: narrow means narrow.

    Submitting output A does not make somebody an editor of outputs generally,
    and holding ``PR_PRODUCTION_EXECUTE`` - which every ``EMPLOYEE`` does - is
    not holding this piece's production. Checked over the router too, because
    "the button was hidden" is not an authorization boundary.
    """
    content_id, master = await ready_to_publish(world)

    with pytest.raises(PrPermissionDeniedError) as raised:
        await correct(world, master, actor=world.other, location=WINDOWS_PATH)
    assert raised.value.details["reason"] == "not_the_submitter"

    world.act_as(world.other)
    response = world.client.patch(
        f"/api/pr/contents/{content_id}/production-outputs/{master.id}",
        json={"location": WINDOWS_PATH},
    )
    assert response.status_code == 403, response.text
    await world.session.refresh(master)
    assert master.location != WINDOWS_PATH


async def test_15_production_management_may_correct_it_too(world: World) -> None:
    """Requirement 15. The lead holds ``PR_PRODUCTION_ASSIGN``, so it is theirs.

    The same predicate that decides who may submit on somebody's behalf. A
    correction is the smaller version of that act.
    """
    _, master = await ready_to_publish(world)
    # The type travels with the location, because the location is judged against
    # it: switching a Drive row to a path without saying so is refused rather
    # than stored, which is why the form sends both.
    fixed = await correct(
        world,
        master,
        actor=world.lead,
        artifact_type=PrProductionArtifactType.NAS_PATH,
        location=RELATIVE_PATH,
    )
    assert fixed.location == RELATIVE_PATH


@pytest.mark.parametrize("reverse_it", [False, True])
async def test_16_17_a_published_output_is_frozen(world: World, reverse_it: bool) -> None:
    """Requirements 16 and 17. Active or reversed, the history still names it.

    A reversed publication says the *record* was wrong; it still records that
    this exact file was posted at some point, so rewriting where it points would
    rewrite what that row says happened. Both cases refuse, with the same reason.
    """
    content_id, master = await ready_to_publish(world)
    publication = await publish(world, content_id, submission=master)
    if reverse_it:
        await world.services.publications.reverse_publication(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            publication_id=publication.id,
        )
        await world.session.refresh(publication)
        assert publication.status is PrPublicationStatus.REVERSED

    with pytest.raises(PrConflictError) as raised:
        await correct(world, master, location=WINDOWS_PATH)
    assert raised.value.details["reason"] == "published_output_is_immutable"
    await world.session.refresh(master)
    assert master.location != WINDOWS_PATH


async def test_17a_the_row_says_so_before_anybody_presses_anything(world: World) -> None:
    """The per-row flag matches the write, so no control is drawn that would 403."""
    content_id, master = await ready_to_publish(world)
    world.act_as(world.member)

    outputs = world.client.get(f"/api/pr/contents/{content_id}/production-outputs").json()
    assert outputs[0]["can_correct"] is True

    await publish(world, content_id, submission=master)
    outputs = world.client.get(f"/api/pr/contents/{content_id}/production-outputs").json()
    assert outputs[0]["can_correct"] is False

    world.act_as(world.other)
    outputs = world.client.get(f"/api/pr/contents/{content_id}/production-outputs").json()
    assert outputs[0]["can_correct"] is False


async def test_18_the_correction_is_audited(world: World) -> None:
    """Requirement 18. The one write to an append-only table, on the record.

    Only what moved appears, and the identity fields appear on neither side
    because they cannot move.
    """
    _, master = await ready_to_publish(world)
    before = master.location

    await correct(
        world, master, artifact_type=PrProductionArtifactType.NAS_PATH, location=WINDOWS_PATH
    )

    rows = await world.session.execute(select(AuditLog).where(AuditLog.entity_id == str(master.id)))
    entry = next(
        row for row in rows.scalars().all() if row.action == "pr.production.submission_corrected"
    )
    assert entry.before_data["location"] == before
    assert entry.after_data["location"] == WINDOWS_PATH
    assert entry.after_data["content_code"]
    assert entry.after_data["submission_no"] == master.submission_no
    for absent in ("producer_user_id", "submitted_by_user_id", "content_version_id"):
        assert absent not in entry.after_data, absent


async def test_19_a_refused_correction_changes_nothing(world: World) -> None:
    """Requirement 19. Validate, then write.

    The location is validated against the type in force *after* the type change
    in the same command, so a request switching to "Google Drive" without a
    Drive URL leaves both fields where they were.
    """
    _, master = await ready_to_publish(world)
    before = (master.artifact_type, master.location, master.note)

    with pytest.raises(PrValidationError):
        await correct(
            world,
            master,
            artifact_type=PrProductionArtifactType.DRIVE_LINK,
            location="https://dropbox.test/x",
            note="đã sửa",
        )

    await world.session.refresh(master)
    assert (master.artifact_type, master.location, master.note) == before
    assert "pr.production.submission_corrected" not in await audit_actions(world, master.id)


async def test_20_the_identity_of_a_submission_cannot_be_touched(world: World) -> None:
    """Requirement 20, and requirement 42 of the step.

    Unrepresentable rather than refused: the command has no field for the
    submission number, the draft it was cut from, or either person, and the
    request model forbids extras - so an attempt is a 422 rather than a silently
    ignored key.
    """
    from meobot.api.schemas.pr import CorrectProductionOutputRequest

    frozen = {
        "submission_no",
        "content_version_id",
        "producer_user_id",
        "submitted_by_user_id",
        "content_id",
    }
    assert frozen.isdisjoint(CorrectSubmissionCommand.__dataclass_fields__)
    assert frozen.isdisjoint(CorrectProductionOutputRequest.model_fields)

    content_id, master = await ready_to_publish(world)
    world.act_as(world.member)
    response = world.client.patch(
        f"/api/pr/contents/{content_id}/production-outputs/{master.id}",
        json={"submission_no": 9},
    )
    assert response.status_code == 422, response.text
    await world.session.refresh(master)
    assert master.submission_no == 1


async def test_20a_archived_production_history_is_read_only(world: World) -> None:
    """``ARCHIVED`` keeps the meaning it has everywhere else in this module."""
    content_id, master = await ready_to_publish(world)
    await force_stage(world, content_id, PrWorkflowStage.ARCHIVED)

    with pytest.raises(PrConflictError) as raised:
        await correct(world, master, location=WINDOWS_PATH)
    assert raised.value.details["reason"] == "archived"


# ===========================================================================
# 21-31: A CONTRIBUTOR RECORDS A PUBLICATION
# ===========================================================================


async def test_21_the_contributor_capability_is_a_member_baseline() -> None:
    """Requirement 21, at the level the decision is actually made.

    Every role holds ``PR_PUBLICATION_CREATE``, because it sits on
    ``script.submit`` - the permission that already means "a contributor's own
    work". ``PR_PUBLICATION_REGISTER`` is unchanged and stays ``ADMIN``-and-above:
    the split is the whole point, and widening the old one would have handed
    contributors everything else ``publish.social`` guards.

    Step 1F.2.3f.3 moved *creating* off this capability and onto the view rule;
    what the capability still marks is the contributor half of **correcting**
    your own row. The assertion below is unchanged either way, and that is the
    point of keeping it: whatever the capability is used for, the two halves of
    the split must not collapse back into one permission.
    """
    from meobot.domain.identity.models import Actor

    for role in Role:
        actor = Actor(user_id=None, full_name="x", role=role)
        assert meets_baseline(actor, PrCapability.PR_PUBLICATION_CREATE), role
    for role in (Role.EMPLOYEE, Role.TEAM_LEAD):
        actor = Actor(user_id=None, full_name="x", role=role)
        assert not meets_baseline(actor, PrCapability.PR_PUBLICATION_REGISTER), role


async def test_21a_a_member_records_a_publication_on_a_channel_they_operate(
    world: World,
) -> None:
    """Requirement 21. The daily task, done by the person who did it.

    The member is assigned to the channel, so it is theirs to post on and theirs
    to record. No administrator is involved anywhere in this test.
    """
    content_id, master = await ready_to_publish(world)
    channel = await new_channel(world, "Kênh của Phương")
    await assign_channel(world, channel, world.member)

    publication = await publish(
        world, content_id, channel_id=channel.id, submission=master, actor=world.member
    )
    assert publication.publisher_user_id == world.member.id
    assert await stage_of(world, content_id) is PrWorkflowStage.PUBLISHED


async def test_21b_a_member_records_one_on_a_planned_channel_for_their_own_piece(
    world: World,
) -> None:
    """The second branch: the plan already named the channel, and the piece is theirs.

    ``world.channel_id`` is this content's planned target and the member owns the
    content, so this is the ordinary case - a writer posting their own piece
    where it was always going to go.
    """
    content_id, master = await ready_to_publish(world)
    row = await world.reload(content_id)
    assert row.owner_user_id == world.member.id

    publication = await publish(world, content_id, submission=master, actor=world.member)
    assert publication.publisher_user_id == world.member.id


async def test_22_a_member_may_now_publish_on_a_channel_that_is_not_theirs(
    world: World,
) -> None:
    """Requirement 22, **superseded by Step 1F.2.3f.3**.

    This test used to assert the opposite, and the rule it asserted is the one
    that step removed. Neither old branch holds here - ``world.other`` has no
    assignment to this channel and the channel is not a planned target of this
    content - and that combination is not an unusual one: it is a colleague
    posting a cut on a channel somebody else runs, which is how the team
    actually works. The old refusal came back as *"Bạn chưa được phép ghi nhận
    bài đăng trên kênh này."* and left two ways round it, an assignment nobody
    meant and a back-dated plan, both of which write a worse fact than the
    publication they were avoiding.

    Kept at its original number rather than deleted, because "requirement 22 of
    Step 1F.2.3f.2 no longer holds" is itself worth being able to read.

    Both paths are asserted - the service and the route - because a member with
    a browser is not the security boundary either way round.
    """
    content_id, master = await ready_to_publish(world)
    somebody_elses = await unplanned_channel(world)

    publication = await publish(
        world, content_id, channel_id=somebody_elses.id, submission=master, actor=world.other
    )
    assert publication.channel_id == somebody_elses.id
    assert publication.publisher_user_id == world.other.id

    second_id, second_master = await ready_to_publish(world)
    world.act_as(world.other)
    response = world.client.post(
        f"/api/pr/contents/{second_id}/publications",
        json={
            "channel_id": str(somebody_elses.id),
            "published_at": NOW.isoformat(),
            "production_submission_id": str(second_master.id),
            "url": POST_URL,
        },
    )
    assert response.status_code == 201, response.text


async def test_22a_management_still_publishes_anywhere(world: World) -> None:
    """``PR_PUBLICATION_REGISTER`` is unrestricted, exactly as before.

    The channel rule is the *contributor* half. Administration keeps the reach it
    has always had, which is what makes back-filling a campaign possible.
    """
    content_id, master = await ready_to_publish(world)
    somebody_elses = await unplanned_channel(world)
    publication = await publish(
        world, content_id, channel_id=somebody_elses.id, submission=master, actor=world.owner
    )
    assert publication.channel_id == somebody_elses.id


async def test_23_a_member_first_publication_publishes_the_content(world: World) -> None:
    """Requirement 23. Recording the first posting still moves the stage.

    Not an approval: it is the consequence of the fact being recorded, and the
    publication row and the transition commit together as they always have.
    """
    content_id, master = await ready_to_publish(world)
    outcome = await world.services.publications.register_publication(
        actor=world.actor(world.member),
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


async def test_24_25_a_member_appends_to_a_published_piece(world: World) -> None:
    """Requirements 24 and 25. Later postings append and move nothing."""
    content_id, master = await ready_to_publish(world)
    await publish(world, content_id, submission=master, actor=world.member)

    second = await new_channel(world, "Kênh thứ hai")
    await assign_channel(world, second, world.member)
    await publish(
        world,
        content_id,
        channel_id=second.id,
        submission=master,
        actor=world.member,
        at=NOW + timedelta(days=1),
    )
    assert await stage_of(world, content_id) is PrWorkflowStage.PUBLISHED

    # A third, later still. Step 1F.2.3f.5 retired ``MEASURED``, which this test
    # used to force the content into - the point was never the stage but that a
    # member keeps being able to append, and ``PUBLISHED`` is where the piece
    # now stays.
    third = await new_channel(world, "Kênh thứ ba")
    await assign_channel(world, third, world.member)
    await publish(
        world,
        content_id,
        channel_id=third.id,
        submission=master,
        actor=world.member,
        at=NOW + timedelta(days=2),
    )
    assert await stage_of(world, content_id) is PrWorkflowStage.PUBLISHED
    assert len(await world.services.publications.list_publications(content_id)) == 3


async def test_26_archived_is_still_refused_for_a_member(world: World) -> None:
    """Requirement 26. Broadening who may record did not broaden when."""
    content_id, master = await ready_to_publish(world)
    await force_stage(world, content_id, PrWorkflowStage.ARCHIVED)

    from meobot.domain.pr.errors import PrWorkflowTransitionError

    with pytest.raises(PrWorkflowTransitionError):
        await publish(world, content_id, submission=master, actor=world.member)


async def test_27_28_output_lineage_is_still_required_and_checked(world: World) -> None:
    """Requirements 27 and 28. Permissions widened; the record did not loosen.

    A contributor still names exactly one produced output, and it still has to
    be one of this content's.
    """
    first_id, first_master = await ready_to_publish(world)
    second_id, _ = await ready_to_publish(world)

    with pytest.raises(PrValidationError) as neither:
        await publish(world, first_id, actor=world.member)
    assert neither.value.details["reason"] == "output_required"

    with pytest.raises(PrValidationError) as foreign:
        await publish(world, second_id, submission=first_master, actor=world.member)
    assert foreign.value.details["reason"] == "foreign_output"

    world.act_as(world.member)
    response = world.client.post(
        f"/api/pr/contents/{second_id}/publications",
        json={
            "channel_id": str(world.channel_id),
            "published_at": NOW.isoformat(),
            "production_submission_id": str(first_master.id),
            "url": POST_URL,
        },
    )
    assert response.status_code == 422, response.text


async def test_29_31_the_record_names_the_person_who_added_the_link(world: World) -> None:
    """Requirements 29, 30 and 31, and the question this has to keep answering.

    *"Ai là người add link đăng bài?"* is answered two ways that must agree:
    ``publisher_user_id`` on the row, and the actor on the audit event. Both are
    the member, not the owner of the content and not whoever happened to hold an
    administrative permission.
    """
    content_id, master = await ready_to_publish(world)
    publication = await publish(world, content_id, submission=master, actor=world.member)

    assert publication.publisher_user_id == world.member.id
    assert publication.url == POST_URL

    rows = await world.session.execute(
        select(AuditLog).where(AuditLog.entity_id == str(publication.id))
    )
    entry = next(row for row in rows.scalars().all() if row.action == "pr.publication.registered")
    assert entry.actor_user_id == world.member.id
    assert entry.after_data["url"] == POST_URL
    assert entry.after_data["channel_id"] == str(world.channel_id)
    assert entry.after_data["production_submission_id"] == str(master.id)


# ===========================================================================
# 32-40: CORRECTING YOUR OWN PUBLICATION
# ===========================================================================


async def test_32_34_the_publisher_corrects_their_own_row(world: World) -> None:
    """Requirements 32, 33 and 34. Link, instant and note - the transcription."""
    from meobot.application.pr_publication_service import UpdatePublicationCommand

    content_id, master = await ready_to_publish(world)
    publication = await publish(world, content_id, submission=master, actor=world.member)
    later = NOW + timedelta(hours=3)

    fixed = await world.services.publications.update_publication(
        actor=world.actor(world.member),
        request_id=world.request_id,
        command=UpdatePublicationCommand(
            publication_id=publication.id,
            url="https://www.tiktok.com/@apexmed/video/7399999999999999999",
            published_at=later,
            note="sửa link sau khi đổi tên kênh",
        ),
    )
    assert fixed.url.endswith("7399999999999999999")
    assert fixed.note == "sửa link sau khi đổi tên kênh"


async def test_35_36_the_channel_and_the_output_stay_frozen_for_everyone(
    world: World,
) -> None:
    """Requirements 35 and 36. Widening who may edit did not widen what.

    Unrepresentable, as in Step 1F.2.3f.1: the command and the request model have
    no field for either, so a contributor cannot rewrite lineage any more than an
    administrator can.
    """
    from meobot.api.schemas.pr import UpdatePublicationRequest
    from meobot.application.pr_publication_service import UpdatePublicationCommand

    frozen = {"channel_id", "production_submission_id", "derivative_id"}
    assert frozen.isdisjoint(UpdatePublicationCommand.__dataclass_fields__)
    assert frozen.isdisjoint(UpdatePublicationRequest.model_fields)

    content_id, master = await ready_to_publish(world)
    publication = await publish(world, content_id, submission=master, actor=world.member)
    world.act_as(world.member)
    response = world.client.patch(
        f"/api/pr/contents/{content_id}/publications/{publication.id}",
        json={"derivative_id": str(uuid.uuid4())},
    )
    assert response.status_code == 422, response.text


async def test_37_another_member_cannot_edit_it(world: World) -> None:
    """Requirement 37, and requirements 65-66 of the step.

    Creating publications does not make somebody an editor of everybody's. The
    row says whose it is - ``publisher_user_id`` - and the server compares it,
    at the service *and* over HTTP.
    """
    from meobot.application.pr_publication_service import UpdatePublicationCommand

    content_id, master = await ready_to_publish(world)
    publication = await publish(world, content_id, submission=master, actor=world.member)

    with pytest.raises(PrPermissionDeniedError) as raised:
        await world.services.publications.update_publication(
            actor=world.actor(world.other),
            request_id=world.request_id,
            command=UpdatePublicationCommand(publication_id=publication.id, url=POST_URL),
        )
    assert raised.value.details["reason"] == "not_the_publisher"

    world.act_as(world.other)
    response = world.client.patch(
        f"/api/pr/contents/{content_id}/publications/{publication.id}",
        json={"url": "https://www.tiktok.com/@someone/video/1"},
    )
    assert response.status_code == 403, response.text


async def test_38_39_management_still_edits_anybody_s(world: World) -> None:
    """Requirements 38 and 39. The Step 1F.2.3f.1 rule, unchanged.

    And the audit names whoever actually made the change, which is the point of
    recording it separately from ``publisher_user_id``: the row still says the
    member posted it, and the trail says the administrator fixed the link.
    """
    from meobot.application.pr_publication_service import UpdatePublicationCommand

    content_id, master = await ready_to_publish(world)
    publication = await publish(world, content_id, submission=master, actor=world.member)

    await world.services.publications.update_publication(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        command=UpdatePublicationCommand(
            publication_id=publication.id, url="https://www.tiktok.com/@apexmed/video/755"
        ),
    )
    rows = await world.session.execute(
        select(AuditLog).where(AuditLog.entity_id == str(publication.id))
    )
    entry = next(row for row in rows.scalars().all() if row.action == "pr.publication.updated")
    assert entry.actor_user_id == world.owner.id
    await world.session.refresh(publication)
    # Attribution for the *posting* is untouched by somebody correcting the link.
    assert publication.publisher_user_id == world.member.id


async def test_40_the_row_says_who_may_touch_it(world: World) -> None:
    """Requirement 40 of the step's UI half, decided on the server.

    Two sessions, one list, two different answers - which is exactly why the flag
    is per row rather than per content.
    """
    content_id, master = await ready_to_publish(world)
    publication = await publish(world, content_id, submission=master, actor=world.member)

    world.act_as(world.member)
    mine = world.client.get(f"/api/pr/contents/{content_id}/publications").json()[0]
    assert mine["can_edit"] is True
    assert mine["can_reverse"] is False

    world.act_as(world.other)
    theirs = world.client.get(f"/api/pr/contents/{content_id}/publications").json()[0]
    assert theirs["can_edit"] is False
    assert theirs["can_reverse"] is False

    world.act_as(world.owner)
    managed = world.client.get(f"/api/pr/contents/{content_id}/publications").json()[0]
    assert managed["can_edit"] is True
    assert managed["can_reverse"] is True
    assert str(publication.id) == managed["id"]


# ===========================================================================
# 41-45: REVERSAL STAYS NARROW
# ===========================================================================


async def test_41_42_a_member_may_not_reverse_anything(world: World) -> None:
    """Requirements 41 and 42. Deliberately not widened by this step.

    Recording a posting is a statement about your own work. Reversing one edits
    the distribution history and can move the content's stage back, which is a
    decision *about the record* rather than a contribution to it - so it stays
    with the people who administer publications, including for the member's own
    row.
    """
    content_id, master = await ready_to_publish(world)
    publication = await publish(world, content_id, submission=master, actor=world.member)

    for actor in (world.member, world.other, world.lead):
        with pytest.raises(PrPermissionDeniedError):
            await world.services.publications.reverse_publication(
                actor=world.actor(actor),
                request_id=world.request_id,
                publication_id=publication.id,
            )

    world.act_as(world.member)
    assert (
        world.client.post(
            f"/api/pr/contents/{content_id}/publications/{publication.id}/reverse"
        ).status_code
        == 403
    )
    await world.session.refresh(publication)
    assert publication.status is PrPublicationStatus.PUBLISHED


async def test_43_45_management_reversal_is_unchanged(world: World) -> None:
    """Requirements 43, 44 and 45. Step 1F.2.3f.1's rules survive intact.

    The owner reverses the member's publication, the content goes back because it
    is safe to, the row stays in the history marked reversed, and the metrics
    safeguard is still in front of it.
    """
    content_id, master = await ready_to_publish(world)
    publication = await publish(world, content_id, submission=master, actor=world.member)

    outcome = await world.services.publications.reverse_publication(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        publication_id=publication.id,
    )
    assert outcome.stage_reverted is True
    assert await stage_of(world, content_id) is PrWorkflowStage.READY_TO_PUBLISH

    rows = await world.services.publications.list_publications(content_id)
    assert len(rows) == 1
    assert rows[0].status is PrPublicationStatus.REVERSED
    # Still evidence, so still blocking a permanent delete.
    from meobot.domain.pr.errors import PrPublishedContentError

    with pytest.raises(PrPublishedContentError):
        await world.services.lifecycle.delete_content(
            actor=world.actor(world.owner), request_id=world.request_id, content_id=content_id
        )


# ===========================================================================
# THE BOARD IS STILL UNTOUCHED
# ===========================================================================


async def test_the_board_never_learned_about_any_of_this(world: World) -> None:
    """Step 1F.2.3c2's lanes are authoritative and this step did not touch them.

    Neither the contributor capability nor the per-row flags reached the work
    queue: a board card carries no publication, output or correction data, and
    the query builder has still never heard of those tables.
    """
    content_id, master = await ready_to_publish(world)
    await publish(world, content_id, submission=master, actor=world.member)

    world.act_as(world.owner)
    body = world.client.get("/api/pr/contents/board", params={"scope": "ALL", "limit": 20}).json()
    assert body["items"], body
    card = body["items"][0]
    for absent in ("publications", "can_edit", "can_correct", "production_outputs"):
        assert absent not in card, absent

    import pathlib

    source = pathlib.Path("src/meobot/application/pr_content_query.py").read_text("utf-8")
    for table in ("PrProductionSubmission", "PrContentDerivative"):
        assert table not in source, table

    # Step 1F.2.3f.4 narrowed the publication half rather than dropping it. The
    # completed board is now scoped to a work month, which cannot be decided
    # without knowing when a piece went out - so ``PrPublication`` appears here
    # as a **correlated scalar subquery** and never as a join. That distinction
    # is the whole of what this guard was protecting: a join multiplies rows, so
    # a piece on three channels would be three cards and the total beside the
    # list would exceed the list.
    assert "scalar_subquery()" in source
    assert ".join(PrPublication" not in source
    assert "join(PrPublication" not in source


async def test_the_stage_never_moved_by_accident(world: World) -> None:
    """Correcting a file and correcting a link are not workflow events.

    Neither writes a transition, and the content stands exactly where it did -
    the property every step in this series has had to keep asserting.
    """
    from meobot.application.pr_publication_service import UpdatePublicationCommand

    content_id, master = await ready_to_publish(world)
    await correct(
        world, master, artifact_type=PrProductionArtifactType.NAS_PATH, location=WINDOWS_PATH
    )
    assert await stage_of(world, content_id) is PrWorkflowStage.READY_TO_PUBLISH

    publication = await publish(world, content_id, submission=master, actor=world.member)
    history = await audit_actions(world, content_id)
    await world.services.publications.update_publication(
        actor=world.actor(world.member),
        request_id=world.request_id,
        command=UpdatePublicationCommand(publication_id=publication.id, note="ghi chú"),
    )
    assert await stage_of(world, content_id) is PrWorkflowStage.PUBLISHED
    assert await audit_actions(world, content_id) == history
    assert await world.session.get(PrContentItem, content_id) is not None
