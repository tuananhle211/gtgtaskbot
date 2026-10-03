"""Step 1F.2.3g - open derivative contributions, and the discussion on a piece.

Numbered 1-46, following the requirement numbering the step was specified with:
1-16 the widened derivative rule, 17-39 comments, 40-46 the security boundary
that says a browser is not an authorization layer.

The world is ``test_pr_production_lifecycle``'s, imported rather than rebuilt,
and so are ``test_pr_derivatives_and_publications``' helpers for reaching
``READY_TO_PUBLISH`` with a real master cut. That is deliberate: this step's
whole subject is who may act on a piece that has *already been produced*, so the
fixture it needs is the one that really produced it - and a second copy of that
walk would eventually disagree with the first about what "produced" means.

The two questions this file exists to keep separate
----------------------------------------------------

**Who may contribute**, and **who may manage**. Step 1F.2.3g widens the first to
everybody who may read the piece and leaves the second exactly where it was, and
almost every test below is one of those two claims:

* an unrelated colleague may record a cutdown, and may not touch the one beside
  it;
* anybody may comment, and nobody may reword somebody else's words;
* a publication still freezes what it points at, for the contributor who
  recorded it and for the manager alike.

And one claim that runs through all of them: **none of this moves anything.**
Recording a derivative and posting a comment leave ``workflow_stage``,
``pr_content_versions`` and every approval exactly as they were, and the tests
say so repeatedly rather than once - because a "collaboration" feature that
quietly nudged the workflow is the failure this step most needed to avoid.
"""

from __future__ import annotations

# The ``world`` fixture and the production walk are imported from two sibling
# modules rather than rebuilt - see the module docstring. pytest requires a
# fixture to be a module-level name, and every test then takes a parameter of the
# same name, so ruff sees a redefinition on every signature in the file.
# ruff: noqa: F811
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from meobot.application.pr_content_asset_service import (
    AddDerivativeCommand,
    UpdateDerivativeCommand,
)
from meobot.application.pr_content_comment_service import AddCommentCommand
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.pr_content_comment import PrContentComment
from meobot.db.models.pr_content_version import PrContentVersion
from meobot.db.models.user import User
from meobot.domain.identity.models import Actor, Role
from meobot.domain.pr.comments import MAX_COMMENT_LENGTH
from meobot.domain.pr.errors import (
    PrConflictError,
    PrNotFoundError,
    PrPermissionDeniedError,
    PrValidationError,
)
from meobot.domain.pr.models import PrContentDerivativeType, PrWorkflowStage
from meobot.domain.pr.policy import PrCapability
from tests.unit.test_pr_derivatives_and_publications import (
    CUT,
    add_derivative,
    derivatives_of,
    force_stage,
    publish,
    ready_to_publish,
    stage_of,
)
from tests.unit.test_pr_production_lifecycle import (  # noqa: F401 - `world` is a fixture
    NOW,
    World,
    audit_actions,
    make_content,
    to_production,
    world,
)

pytestmark = pytest.mark.asyncio


# --- Helpers ----------------------------------------------------------------


async def comment(
    world: World,
    content_id: uuid.UUID,
    *,
    actor: User,
    body: str = "Hook đoạn đầu hơi dài, cắt còn 3 giây nhé.",
    parent: PrContentComment | uuid.UUID | None = None,
) -> PrContentComment:
    parent_id = parent.id if isinstance(parent, PrContentComment) else parent
    return await world.services.content_comments.add_comment(
        actor=world.actor(actor),
        request_id=world.request_id,
        command=AddCommentCommand(content_id=content_id, body=body, parent_comment_id=parent_id),
    )


async def thread(world: World, content_id: uuid.UUID, *, actor: User, **kwargs: int):
    return await world.services.content_comments.list_comments(
        actor=world.actor(actor), content_id=content_id, **kwargs
    )


async def views(world: World, content_id: uuid.UUID, *, actor: User):
    """The derivative read model, as one person sees it."""
    return await world.services.content_assets.describe_derivatives(
        actor=world.actor(actor), content_id=content_id
    )


def blind(role: Role = Role.EMPLOYEE) -> Actor:
    """An actor with a role that holds nothing.

    Every one of MeoBot's four roles carries ``script.read``, which is the
    permission PR content is read through - so "somebody who cannot view this
    content" is not a role, and constructing one honestly means removing the
    permission rather than picking a different role and pretending. See
    requirement 6, which patches the matrix for exactly one call.
    """
    return Actor(user_id=uuid.uuid4(), full_name="Người ngoài", role=role)


async def outsider(world: World, name: str = "Trần Người Ngoài") -> User:
    """A plain ``EMPLOYEE`` with no relationship to anything.

    Needed because ``world.head`` is not one, which is easy to get wrong:
    ``ADMIN`` holds ``video.approve``, therefore ``PR_PRODUCTION_ASSIGN``,
    therefore *is* production management on every item - and holds
    ``script.approve``, therefore ``PR_CONTENT_CANCEL``, therefore moderates
    comments. Both are correct and both make them the wrong actor for a "an
    unrelated person may not" test.

    ``world.other`` is the right shape but is usually the author in these tests,
    so this is the second one.
    """
    person = User(full_name=name, role=Role.EMPLOYEE)
    world.session.add(person)
    await world.session.flush()
    return person


async def versions_of(world: World, content_id: uuid.UUID) -> int:
    found = await world.session.execute(
        select(func.count())
        .select_from(PrContentVersion)
        .where(PrContentVersion.content_id == content_id)
    )
    return int(found.scalar() or 0)


# ===========================================================================
# 1-8: WHO MAY RECORD A DERIVATIVE, AND WHAT IS RECORDED ABOUT THEM
# ===========================================================================


async def test_01_an_ordinary_viewer_may_add_a_derivative(world: World) -> None:
    """Requirement 1. The change this step exists for.

    ``world.other`` is an ``EMPLOYEE`` who owns nothing here, is assigned to
    nothing, produces nothing and holds no review grant. Before this step they
    were refused with ``not_the_producer``; they are the person who actually made
    the October cutdown.
    """
    content_id, _ = await ready_to_publish(world)
    recorded = await add_derivative(world, content_id, actor=world.other)
    assert recorded.created_by_user_id == world.other.id
    assert [row.id for row in await derivatives_of(world, content_id)] == [recorded.id]


@pytest.mark.parametrize("who", ["owner", "member", "lead", "head", "other"])
async def test_02_05_owner_responsible_producer_and_stranger_all_may(
    world: World, who: str
) -> None:
    """Requirements 2, 3, 4 and 5, in one table because they are one rule.

    The content's owner, the person responsible for it, its producer, a reviewer
    and an unrelated colleague are five different relationships to the piece, and
    since this step **not one of them is consulted**. Parametrised rather than
    written five times precisely to make that visible: if any relationship ever
    started mattering again, four of these would still pass.

    ``world.member`` is both the owner and the producer here - ``ready_to_publish``
    walks them through it - which is why the two cases are not separated further.
    """
    content_id, _ = await ready_to_publish(world)
    actor: User = getattr(world, who)
    recorded = await add_derivative(world, content_id, actor=actor, label=f"Cut {who}")
    assert recorded.created_by_user_id == actor.id


async def test_06_somebody_who_cannot_view_the_content_cannot_add(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requirement 6. The rule is the *view* rule, and this is what proves it.

    Every MeoBot role carries ``script.read``, so there is no role that cannot
    see PR content and no honest way to build such an actor. What there is, is
    the gate itself - so the permission is removed for the duration of one call
    and both the read and the write are asked.

    **Both refuse, for the same reason.** That is the claim: contributing a
    derivative is not a second, laxer authorization path that happens to be open
    - it is the same check ``PrQueryService`` makes before it will show the piece
    at all, which is why a future narrowing of "who may view" narrows
    contribution with it.
    """
    content_id, _ = await ready_to_publish(world)
    monkeypatch.setattr("meobot.domain.pr.policy.has_permission", lambda role, permission: False)

    with pytest.raises(PrPermissionDeniedError):
        await world.services.queries.get_content(
            actor=world.actor(world.other), content_id=content_id
        )
    with pytest.raises(PrPermissionDeniedError):
        await add_derivative(world, content_id, actor=world.other)
    with pytest.raises(PrPermissionDeniedError):
        await world.services.content_assets.describe_derivatives(
            actor=world.actor(world.other), content_id=content_id
        )
    assert await derivatives_of(world, content_id) == []


async def test_07_the_row_itself_remembers_who_recorded_it(world: World) -> None:
    """Requirement 7. Attribution is a column, not an inference.

    ``created_by_user_id`` is on the row and ``NOT NULL``, so "who added this
    cut" survives any pruning of the audit trail and is answerable by the same
    query that returns the list. The read model turns it into a name in one
    join - never a UUID on screen.
    """
    content_id, _ = await ready_to_publish(world)
    recorded = await add_derivative(world, content_id, actor=world.other)
    await world.session.refresh(recorded)
    assert recorded.created_by_user_id == world.other.id

    seen = await views(world, content_id, actor=world.owner)
    assert [view.created_by_name for view in seen] == [world.other.full_name]


async def test_08_the_audit_actor_is_the_account_that_added_it(world: World) -> None:
    """Requirement 8. Row-level attribution *and* a trail, naming the same person.

    Two records of one act, and they must agree: the row says whose cut it is,
    the audit row says who filed it, and a step that widened who may file made it
    worth asserting that widening did not detach the second from the first.
    """
    content_id, _ = await ready_to_publish(world)
    recorded = await add_derivative(world, content_id, actor=world.other)

    rows = (
        (
            await world.session.execute(
                select(AuditLog).where(AuditLog.entity_id == str(recorded.id))
            )
        )
        .scalars()
        .all()
    )
    assert [row.action for row in rows] == ["pr.content.derivative_added"]
    assert rows[0].actor_user_id == world.other.id
    assert rows[0].after_data is not None
    assert rows[0].after_data["label"] == recorded.label


# ===========================================================================
# 9-12: CORRECTING AND REMOVING - CONTRIBUTION IS NOT MANAGEMENT
# ===========================================================================


async def test_09_the_recorder_may_correct_their_own(world: World) -> None:
    """Requirement 9. The other half of being allowed to add one.

    Somebody who may record a cut and may not fix the label they mistyped on it
    an hour later has been given a feature they cannot use, so the recorder is a
    first-class editor of their own row - and of nothing else.
    """
    content_id, _ = await ready_to_publish(world)
    recorded = await add_derivative(world, content_id, actor=world.other)

    corrected = await world.services.content_assets.update_derivative(
        actor=world.actor(world.other),
        request_id=world.request_id,
        command=UpdateDerivativeCommand(derivative_id=recorded.id, label="TikTok cut 25s (v2)"),
    )
    assert corrected.label == "TikTok cut 25s (v2)"

    mine = await views(world, content_id, actor=world.other)
    assert [view.can_edit for view in mine] == [True]
    assert [view.can_delete for view in mine] == [True]


async def test_10_an_unrelated_viewer_may_not_touch_somebody_elses(world: World) -> None:
    """Requirement 10. The line between contributing and managing.

    A colleague who may view the piece may therefore record a derivative of their
    own. That gets them nothing at all over the row ``world.other`` recorded -
    not a correction, not a deletion - and the read model says so before they
    try.

    Deliberately an ``outsider`` rather than ``world.head``: an ``ADMIN`` holds
    ``video.approve`` and is therefore production management on every item, so
    they *may* edit this row and are the wrong actor for this question.
    """
    content_id, _ = await ready_to_publish(world)
    theirs = await add_derivative(world, content_id, actor=world.other)
    stranger = await outsider(world)

    with pytest.raises(PrPermissionDeniedError) as raised:
        await world.services.content_assets.update_derivative(
            actor=world.actor(stranger),
            request_id=world.request_id,
            command=UpdateDerivativeCommand(derivative_id=theirs.id, label="Của tôi bây giờ"),
        )
    assert raised.value.details["reason"] == "not_the_contributor"

    with pytest.raises(PrPermissionDeniedError):
        await world.services.content_assets.delete_derivative(
            actor=world.actor(stranger), request_id=world.request_id, derivative_id=theirs.id
        )

    seen = await views(world, content_id, actor=stranger)
    assert [view.can_edit for view in seen] == [False]
    assert [view.can_delete for view in seen] == [False]
    # And they may still add one of their own: contribution is untouched.
    assert await add_derivative(world, content_id, actor=stranger, label="Bản của người ngoài")


async def test_11_production_management_still_edits_anybodys(world: World) -> None:
    """Requirement 11. The pre-existing authority, unchanged.

    ``world.lead`` holds ``PR_PRODUCTION_ASSIGN``, which has meant "may manage
    this item's production output" since Step 1F.2.3f. Opening who may *add* a
    row did not take that away, and this is the test that would fail if a
    tidy-up ever replaced the manager branch with the recorder one.
    """
    content_id, _ = await ready_to_publish(world)
    theirs = await add_derivative(world, content_id, actor=world.other)

    corrected = await world.services.content_assets.update_derivative(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        command=UpdateDerivativeCommand(derivative_id=theirs.id, label="Sửa bởi quản lý"),
    )
    assert corrected.label == "Sửa bởi quản lý"
    assert [view.can_edit for view in await views(world, content_id, actor=world.lead)] == [True]


async def test_12_a_published_output_stays_frozen_for_everybody(world: World) -> None:
    """Requirement 12. The historical-safety rule, **not** weakened by any of this.

    Once a publication names a file, that row is part of the answer to "what did
    we actually post in October". So the identity-bearing fields are frozen and
    the row cannot be deleted - and the recorder is not an exception, which is
    the case this step could plausibly have broken: they added it, and they still
    may not rewrite where it points once something went out from it.

    The human fields stay editable, exactly as before: fixing a typo in a label
    corrects how the row reads and changes nothing about what happened.
    """
    content_id, master = await ready_to_publish(world)
    theirs = await add_derivative(world, content_id, actor=world.other)
    await publish(world, content_id, submission=master)
    await publish(world, content_id, derivative=theirs, url="https://example.com/p/2")

    for actor in (world.other, world.lead):
        with pytest.raises(PrConflictError) as raised:
            await world.services.content_assets.update_derivative(
                actor=world.actor(actor),
                request_id=world.request_id,
                command=UpdateDerivativeCommand(
                    derivative_id=theirs.id, location="https://drive.google.com/file/d/other/view"
                ),
            )
        assert raised.value.details["reason"] == "published_output_is_immutable"
        assert raised.value.details["editable"] == ["label", "note"]

        with pytest.raises(PrConflictError):
            await world.services.content_assets.delete_derivative(
                actor=world.actor(actor), request_id=world.request_id, derivative_id=theirs.id
            )

    # The label still moves, for its recorder.
    fixed = await world.services.content_assets.update_derivative(
        actor=world.actor(world.other),
        request_id=world.request_id,
        command=UpdateDerivativeCommand(derivative_id=theirs.id, label="TikTok cut 25s"),
    )
    assert fixed.label == "TikTok cut 25s"

    # And the read model says all of it before anybody presses anything.
    seen = next(view for view in await views(world, content_id, actor=world.other))
    assert seen.is_published_output is True
    assert seen.can_edit is True
    assert seen.can_delete is False


# ===========================================================================
# 13-16: WHAT ADDING ONE DOES TO THE WORKFLOW, AND WHERE IT IS ALLOWED
# ===========================================================================


async def test_13_recording_a_derivative_moves_nothing(world: World) -> None:
    """Requirement 13. The promise the whole step rests on.

    No stage, no version, no approval. Asserted about a *stranger's* contribution
    specifically, because "anybody may write to this content item" is the shape
    that would make an accidental workflow effect dangerous.
    """
    content_id, _ = await ready_to_publish(world)
    before_stage = await stage_of(world, content_id)
    before_versions = await versions_of(world, content_id)
    before_actions = await audit_actions(world, content_id)

    await add_derivative(world, content_id, actor=world.other)

    assert await stage_of(world, content_id) is before_stage
    assert await versions_of(world, content_id) == before_versions
    # One new audit row, and it is the derivative's own - no transition, no
    # approval, no version event.
    assert await audit_actions(world, content_id) == before_actions


@pytest.mark.parametrize(
    "stage",
    [PrWorkflowStage.PUBLISHED, PrWorkflowStage.MEASURED, PrWorkflowStage.ARCHIVED],
)
async def test_14_16_late_stage_content_still_takes_a_strangers_cut(
    world: World, stage: PrWorkflowStage
) -> None:
    """Requirements 14, 15 and 16, and the ``ARCHIVED`` answer stated outright.

    All three, and **``ARCHIVED`` included** - which is the existing rule rather
    than a widening of it. ``ARCHIVED`` in this repository is precise and
    narrower than "frozen": it is terminal for *transitions* and it blocks
    *deletion*, and it has never stopped priority, content type, review
    resources, derivatives or destination links being written. Step 1F.2.3f
    settled that for derivatives; 1F.2.3g changes who may write one, not where.

    Recording, six months later, the cutdown a colleague actually made is
    correcting the record of an archived piece rather than reopening it - and
    refusing it would leave the team with the one workaround this whole feature
    exists to remove: cloning the content.
    """
    content_id, _ = await ready_to_publish(world)
    await force_stage(world, content_id, stage)

    recorded = await add_derivative(
        world, content_id, actor=world.other, label=f"Cut {stage.value}"
    )
    assert recorded.created_by_user_id == world.other.id
    assert await stage_of(world, content_id) is stage

    kinds = await world.actions(world.other, content_id)
    assert "ADD_CONTENT_DERIVATIVE" in kinds


# ===========================================================================
# 17-23: READING AND WRITING A COMMENT
# ===========================================================================


async def test_17_any_viewer_can_read_the_thread(world: World) -> None:
    """Requirement 17. One permission for the conversation, and it is the read.

    Nobody has to be granted anything to follow a discussion about a piece they
    can already see - a separate comment-read model would be a second, weaker
    copy of the visibility rule and the two would drift.
    """
    content_id, _ = await ready_to_publish(world)
    await comment(world, content_id, actor=world.member)

    for actor in (world.owner, world.lead, world.head, world.other):
        page = await thread(world, content_id, actor=actor)
        assert [item.body for item in page.threads] == [
            "Hook đoạn đầu hơi dài, cắt còn 3 giây nhé."
        ]


async def test_18_somebody_who_cannot_view_reads_nothing(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requirement 18. Same gate as the content, in both directions.

    Read and write take the identical check, so there is no state in which a
    person can see a conversation and not join it, or join one they cannot see.
    """
    content_id, _ = await ready_to_publish(world)
    await comment(world, content_id, actor=world.member)
    monkeypatch.setattr("meobot.domain.pr.policy.has_permission", lambda role, permission: False)

    with pytest.raises(PrPermissionDeniedError):
        await thread(world, content_id, actor=world.other)
    with pytest.raises(PrPermissionDeniedError):
        await comment(world, content_id, actor=world.other, body="Xin chào")


async def test_19_20_a_viewer_may_post_and_the_row_names_them(world: World) -> None:
    """Requirements 19 and 20. No capability, and the author is a column.

    ``world.other`` holds no review grant, owns nothing here and produces
    nothing. They may say something, and the row remembers who said it - stored
    directly rather than inferred from an audit trail that does not record
    creation at all.
    """
    content_id, _ = await ready_to_publish(world)
    said = await comment(world, content_id, actor=world.other)

    await world.session.refresh(said)
    assert said.author_user_id == world.other.id
    assert said.parent_comment_id is None
    assert said.deleted_at is None
    assert said.edited_at is None

    page = await thread(world, content_id, actor=world.owner)
    assert [item.author_name for item in page.threads] == [world.other.full_name]


@pytest.mark.parametrize("body", ["", "   ", "\n\t  \n"])
async def test_21_22_an_empty_or_whitespace_comment_is_refused(world: World, body: str) -> None:
    """Requirements 21 and 22. Nothing is a comment.

    Trimmed first, so ``"   "`` and ``""`` are the same refusal rather than one
    validation error and one blank row - ``"   "`` is what a form submits when
    somebody tabs past the box and presses Gửi.
    """
    content_id, _ = await ready_to_publish(world)
    with pytest.raises(PrValidationError) as raised:
        await comment(world, content_id, actor=world.other, body=body)
    assert raised.value.details == {"field": "body", "reason": "empty"}
    assert await thread(world, content_id, actor=world.owner) is not None


async def test_23_an_over_long_comment_is_refused_with_the_limit(world: World) -> None:
    """Requirement 23. Long enough for a paragraph, short of a document.

    The refusal carries ``max_length`` so a client can word *"tối đa 2000 ký
    tự"* without keeping its own copy of the number. Exactly at the limit is
    accepted - an off-by-one here is the kind that only shows up in the one
    comment somebody spent ten minutes on.
    """
    content_id, _ = await ready_to_publish(world)
    assert await comment(world, content_id, actor=world.other, body="x" * MAX_COMMENT_LENGTH)

    with pytest.raises(PrValidationError) as raised:
        await comment(world, content_id, actor=world.other, body="y" * (MAX_COMMENT_LENGTH + 1))
    assert raised.value.details["reason"] == "too_long"
    assert raised.value.details["max_length"] == MAX_COMMENT_LENGTH


# ===========================================================================
# 24-26: THREADING, AND WHY IT STOPS AT ONE LEVEL
# ===========================================================================


async def test_24_a_viewer_may_reply_to_a_root(world: World) -> None:
    """Requirement 24. A reply is a comment with a parent, and reads as one.

    The reply comes back **nested under its root** rather than as a sibling with
    an id to match up, so a client renders a thread without assembling one.
    """
    content_id, _ = await ready_to_publish(world)
    root = await comment(world, content_id, actor=world.member)
    reply = await comment(
        world, content_id, actor=world.other, body="Đã sửa bản cut mới.", parent=root
    )
    assert reply.parent_comment_id == root.id

    page = await thread(world, content_id, actor=world.owner)
    assert len(page.threads) == 1
    assert page.total == 1
    assert [item.body for item in page.threads[0].replies] == ["Đã sửa bản cut mới."]


async def test_25_a_parent_from_another_content_item_is_refused(world: World) -> None:
    """Requirement 25. The parent is checked, not trusted.

    Without this, a reply could be filed against a thread on a different piece of
    work - the comment would appear in a conversation nobody in it had joined,
    and the URL that fetched it would never show it.
    """
    first, _ = await ready_to_publish(world)
    second = await make_content(world, owner=world.member, title="Bài khác")
    elsewhere = await comment(world, second, actor=world.member)

    with pytest.raises(PrValidationError) as raised:
        await comment(world, first, actor=world.other, body="Nhầm bài", parent=elsewhere)
    assert raised.value.details["reason"] == "foreign_comment"
    assert (await thread(world, first, actor=world.owner)).total == 0


async def test_26_a_reply_to_a_reply_is_refused_not_reparented(world: World) -> None:
    """Requirement 26. Depth stops at one, and the refusal is explicit.

    Silently attaching it to the root was the other option and is worse: it moves
    somebody's answer under a different question, and they would have no way to
    tell it had happened. The refusal carries the root's id, so a client can
    offer to post it there instead rather than only apologising.
    """
    content_id, _ = await ready_to_publish(world)
    root = await comment(world, content_id, actor=world.member)
    reply = await comment(world, content_id, actor=world.other, body="Trả lời", parent=root)

    with pytest.raises(PrValidationError) as raised:
        await comment(world, content_id, actor=world.member, body="Trả lời lồng", parent=reply)
    assert raised.value.details["reason"] == "nested_reply"
    assert raised.value.details["root_comment_id"] == str(root.id)

    page = await thread(world, content_id, actor=world.owner)
    assert len(page.threads[0].replies) == 1


# ===========================================================================
# 27-33: EDITING, DELETING, AND WHAT A TOMBSTONE LEAVES BEHIND
# ===========================================================================


async def test_27_the_author_may_reword_their_own(world: World) -> None:
    """Requirement 27. And ``edited_at`` is its own column for a reason.

    ``updated_at`` also moves when a comment is tombstoned, so *"đã sửa"* is
    drawn from a field that means only one thing. A form submitted unedited
    writes nothing at all, so nothing claims a rewording that did not happen.
    """
    content_id, _ = await ready_to_publish(world)
    said = await comment(world, content_id, actor=world.other)

    unchanged = await world.services.content_comments.update_comment(
        actor=world.actor(world.other),
        request_id=world.request_id,
        content_id=content_id,
        comment_id=said.id,
        body="Hook đoạn đầu hơi dài, cắt còn 3 giây nhé.",
    )
    assert unchanged.edited_at is None

    reworded = await world.services.content_comments.update_comment(
        actor=world.actor(world.other),
        request_id=world.request_id,
        content_id=content_id,
        comment_id=said.id,
        body="Hook hơi dài, cắt còn 3 giây nhé.",
    )
    assert reworded.body == "Hook hơi dài, cắt còn 3 giây nhé."
    assert reworded.edited_at is not None


async def test_28_nobody_else_may_reword_it_not_even_management(world: World) -> None:
    """Requirement 28. Moderation stops short of putting words in a mouth.

    ``world.owner`` holds every permission and every management capability, and
    still cannot change what somebody said - because the result would be a
    sentence in that person's name that they never wrote, which is worse than
    anything it could fix. Removal is the moderator's tool; see requirement 30.
    """
    content_id, _ = await ready_to_publish(world)
    said = await comment(world, content_id, actor=world.other)

    for actor in (world.member, world.lead, world.owner):
        with pytest.raises(PrPermissionDeniedError) as raised:
            await world.services.content_comments.update_comment(
                actor=world.actor(actor),
                request_id=world.request_id,
                content_id=content_id,
                comment_id=said.id,
                body="Tôi đổi lời của bạn",
            )
        assert raised.value.details["reason"] == "not_the_author"

    await world.session.refresh(said)
    assert said.body == "Hook đoạn đầu hơi dài, cắt còn 3 giây nhé."
    page = await thread(world, content_id, actor=world.owner)
    assert page.threads[0].can_edit is False


async def test_29_30_the_author_deletes_theirs_a_stranger_deletes_nothing(
    world: World,
) -> None:
    """Requirements 29 and 30, and the moderator exception between them.

    Three answers: its author may take it down, an unrelated colleague may not,
    and a moderator may - where "moderator" is the existing
    ``PR_CONTENT_CANCEL``, the capability that has meant *"may end this piece of
    work"* since Step 1C.1 and is already the management half of content
    deletion. No new capability was invented for this.
    """
    content_id, _ = await ready_to_publish(world)
    mine = await comment(world, content_id, actor=world.other)
    theirs = await comment(world, content_id, actor=world.member, body="Của người khác")

    with pytest.raises(PrPermissionDeniedError) as raised:
        await world.services.content_comments.delete_comment(
            actor=world.actor(world.other),
            request_id=world.request_id,
            content_id=content_id,
            comment_id=theirs.id,
        )
    assert raised.value.details["capability"] == PrCapability.PR_CONTENT_CANCEL.value

    removed = await world.services.content_comments.delete_comment(
        actor=world.actor(world.other),
        request_id=world.request_id,
        content_id=content_id,
        comment_id=mine.id,
    )
    assert removed.deleted_by_user_id == world.other.id

    # The lead holds PR_CONTENT_CANCEL, so moderation works on somebody else's.
    moderated = await world.services.content_comments.delete_comment(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        content_id=content_id,
        comment_id=theirs.id,
    )
    assert moderated.deleted_by_user_id == world.lead.id


async def test_31_deletion_is_a_tombstone_and_is_audited(world: World) -> None:
    """Requirement 31. The row survives; the words stop being sent.

    Three claims: the row is still there with its author and its created instant,
    the read model sends neither the body nor the name, and the removal is the
    **one** comment event in the audit trail - creating and editing write none,
    because a trail entry per typed sentence buries what somebody searches it for.
    """
    content_id, _ = await ready_to_publish(world)
    said = await comment(world, content_id, actor=world.other)
    created_at = said.created_at

    await world.services.content_comments.delete_comment(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        content_id=content_id,
        comment_id=said.id,
    )

    row = await world.session.get(PrContentComment, said.id)
    assert row is not None
    assert row.deleted_at is not None
    assert row.author_user_id == world.other.id
    assert row.body == "Hook đoạn đầu hơi dài, cắt còn 3 giây nhé."
    assert row.created_at == created_at

    view = (await thread(world, content_id, actor=world.owner)).threads[0]
    assert view.is_deleted is True
    assert view.body is None
    assert view.author_name is None
    assert view.author_user_id is None
    assert view.created_at == created_at
    assert (view.can_edit, view.can_delete) == (False, False)

    logged = (
        (
            await world.session.execute(
                select(AuditLog).where(AuditLog.entity_type == "pr_content_comment")
            )
        )
        .scalars()
        .all()
    )
    assert [row.action for row in logged] == ["pr.content.comment_deleted"]
    assert logged[0].actor_user_id == world.lead.id
    assert logged[0].after_data is not None
    assert logged[0].after_data["removed_by"] == "moderator"
    # Never the words. The trail is not a second copy of what was taken down.
    assert "body" not in logged[0].after_data


async def test_32_33_replies_survive_a_deleted_root_and_it_still_renders(
    world: World,
) -> None:
    """Requirements 32 and 33. Somebody's answers are not theirs to delete.

    A hard delete would either orphan the replies or take them with the question,
    and both destroy other people's words to honour one person's decision about
    their own. So the root stays as a gap the panel words *"Đã xoá bình luận."*,
    with the answers readable underneath it.
    """
    content_id, _ = await ready_to_publish(world)
    root = await comment(world, content_id, actor=world.member)
    await comment(world, content_id, actor=world.other, body="Đã sửa bản cut mới.", parent=root)
    await comment(world, content_id, actor=world.head, body="Ok nhé.", parent=root)

    await world.services.content_comments.delete_comment(
        actor=world.actor(world.member),
        request_id=world.request_id,
        content_id=content_id,
        comment_id=root.id,
    )

    page = await thread(world, content_id, actor=world.owner)
    assert page.total == 1
    tombstone = page.threads[0]
    assert tombstone.is_deleted is True
    assert tombstone.id == root.id
    # A set: what this test is about is *survival*, and every comment in it
    # shares one transaction and therefore one ``now()``, which makes their
    # relative order the ``id`` tiebreaker's rather than the clock's. Ordering is
    # requirement 37's subject and is asserted there against distinct instants.
    assert {reply.body for reply in tombstone.replies} == {"Đã sửa bản cut mới.", "Ok nhé."}
    assert all(reply.is_deleted is False for reply in tombstone.replies)


# ===========================================================================
# 34-39: STAGE, WORKFLOW, ORDERING, QUERY COUNT AND THE AGGREGATE DELETE
# ===========================================================================


@pytest.mark.parametrize("stage", list(PrWorkflowStage))
async def test_34_comments_work_at_every_stage_archived_included(
    world: World, stage: PrWorkflowStage
) -> None:
    """Requirement 34, and the ``ARCHIVED`` rule reported rather than assumed.

    Every stage in the enum, and the answer for ``ARCHIVED`` is **the same as
    the rest**: comments may be read, written, reworded and taken down there.

    That is this repository's archive policy applied consistently, not a
    widening of it. ``ARCHIVED`` is terminal for transitions and blocks deletion,
    and has never blocked writing *about* a piece - priority, content type,
    review resources, derivatives and destination links are all stage-independent.
    A discussion that went read-only the moment a piece was put away would go
    silent at exactly the point somebody asks "why did we archive this one?".
    """
    content_id, _ = await ready_to_publish(world)
    await force_stage(world, content_id, stage)

    said = await comment(world, content_id, actor=world.other, body=f"Ở bước {stage.value}")
    reworded = await world.services.content_comments.update_comment(
        actor=world.actor(world.other),
        request_id=world.request_id,
        content_id=content_id,
        comment_id=said.id,
        body=f"Ở bước {stage.value}, đã sửa",
    )
    assert reworded.edited_at is not None
    await world.services.content_comments.delete_comment(
        actor=world.actor(world.other),
        request_id=world.request_id,
        content_id=content_id,
        comment_id=said.id,
    )
    assert await stage_of(world, content_id) is stage
    assert "ADD_CONTENT_COMMENT" in await world.actions(world.other, content_id)


async def test_35_36_commenting_changes_no_stage_and_writes_no_version(
    world: World,
) -> None:
    """Requirements 35 and 36. Discussion is not a decision.

    The failure this guards against is the plausible one: somebody types *"ok
    duyệt nhé"* and something opens a gate. Nothing here can - the comment
    service is not given a workflow service to call, and this asserts the
    consequence.
    """
    content_id, _ = await ready_to_publish(world)
    before_stage = await stage_of(world, content_id)
    before_versions = await versions_of(world, content_id)
    before_actions = await audit_actions(world, content_id)

    root = await comment(world, content_id, actor=world.other, body="Duyệt nhé")
    await comment(world, content_id, actor=world.member, body="Ừ", parent=root)
    await world.services.content_comments.update_comment(
        actor=world.actor(world.other),
        request_id=world.request_id,
        content_id=content_id,
        comment_id=root.id,
        body="Duyệt nhé anh",
    )

    assert await stage_of(world, content_id) is before_stage
    assert await versions_of(world, content_id) == before_versions
    assert await audit_actions(world, content_id) == before_actions


async def test_37_the_order_is_oldest_first_and_total(world: World) -> None:
    """Requirement 37. A conversation reads the way it happened.

    ``created_at`` ascending with ``id`` as the tiebreaker, roots and replies
    alike - so the order is **total**, and three comments written inside one
    transaction (which is every one of these, and every batch a real request
    writes) do not come back in an arbitrary order.

    Paging is over roots, so ``total`` agrees with what paging through produces
    rather than counting replies nobody paged past.
    """
    content_id, _ = await ready_to_publish(world)
    roots = []
    for index in range(5):
        roots.append(await comment(world, content_id, actor=world.other, body=f"Ý {index}"))
    first = await comment(world, content_id, actor=world.member, body="Trả lời 1", parent=roots[0])
    second = await comment(world, content_id, actor=world.member, body="Trả lời 2", parent=roots[0])

    # Stamped explicitly, because the whole batch above shares one transaction
    # and therefore one ``now()`` - which is not what production produces, where
    # each comment arrives in its own request a few seconds apart. ``id`` remains
    # the tiebreaker and makes the order **total**; it is not a second sort key
    # that means anything, and this is what stops the test from asserting that a
    # random UUID sorts chronologically.
    for offset, row in enumerate([*roots, first, second]):
        row.created_at = datetime(2026, 10, 18, 3, offset, tzinfo=UTC)
    await world.session.flush()

    page = await thread(world, content_id, actor=world.owner)
    assert [item.body for item in page.threads] == [f"Ý {index}" for index in range(5)]
    assert [reply.body for reply in page.threads[0].replies] == ["Trả lời 1", "Trả lời 2"]
    assert page.total == 5

    second = await thread(world, content_id, actor=world.owner, limit=2, offset=2)
    assert [item.body for item in second.threads] == ["Ý 2", "Ý 3"]
    assert (second.total, second.limit, second.offset) == (5, 2, 2)


async def test_38_a_thread_costs_a_fixed_number_of_queries(world: World) -> None:
    """Requirement 38. No query per root, and none per author.

    Counted rather than reasoned about, because "avoid N+1" is a claim that
    quietly stops being true. The statement count for a ten-root thread is the
    same as for a one-root thread; if somebody ever fetches replies inside the
    loop, this fails by exactly the amount they cost.
    """
    content_id, _ = await ready_to_publish(world)

    async def count_statements(roots: int) -> int:
        for index in range(roots):
            root = await comment(world, content_id, actor=world.other, body=f"Chủ đề {index}")
            await comment(world, content_id, actor=world.member, body="Trả lời", parent=root)
        await world.session.flush()

        statements: list[str] = []

        def record(*args: object, **kwargs: object) -> None:
            statements.append("x")

        from sqlalchemy import event

        engine = world.session.get_bind().engine  # type: ignore[union-attr]
        event.listen(engine, "before_cursor_execute", record)
        try:
            await thread(world, content_id, actor=world.owner, limit=50)
        finally:
            event.remove(engine, "before_cursor_execute", record)
        return len(statements)

    one = await count_statements(1)
    ten = await count_statements(9)
    assert one == ten, f"{one} statements for one root, {ten} for ten"


async def test_39_a_permanent_content_delete_takes_the_thread_with_it(
    world: World,
) -> None:
    """Requirement 39. Comments are content-owned, and go where the content goes.

    The aggregate delete removes roots and replies together in one statement -
    which is safe under the self-referencing ``RESTRICT`` because a ``RESTRICT``
    check is applied to what is still standing at the end of the statement, and
    the predicate matches by ``content_id``, so a root and its answers are always
    in the same pass.

    Deleted with the content rather than left behind: a conversation about a
    piece nobody can look up is a set of rows nothing can reach.
    """
    content_id = await make_content(world, owner=world.member, title="Nháp bỏ đi")
    root = await comment(world, content_id, actor=world.other)
    await comment(world, content_id, actor=world.member, body="Trả lời", parent=root)

    receipt = await world.services.lifecycle.delete_content(
        actor=world.actor(world.member), request_id=world.request_id, content_id=content_id
    )
    assert receipt.removed.get("pr_content_comments") == 2
    remaining = await world.session.execute(
        select(func.count())
        .select_from(PrContentComment)
        .where(PrContentComment.content_id == content_id)
    )
    assert int(remaining.scalar() or 0) == 0


# ===========================================================================
# 40-46: THE SECURITY BOUNDARY - A BROWSER IS NOT AN AUTHORIZATION LAYER
# ===========================================================================


async def test_40_the_offers_are_a_courtesy_and_the_routes_are_the_control(
    world: World,
) -> None:
    """Requirement 40. What ``/available-actions`` is for, and what it is not.

    The two new offers reach everybody who may read the piece, which is the
    point - but they are drawn from the *write's* predicate, so the list and the
    routes cannot disagree. Every test below posts straight at the API, because
    "the panel hides this button" is not a control.
    """
    content_id, _ = await ready_to_publish(world)
    for user in (world.owner, world.lead, world.head, world.member, world.other):
        kinds = await world.actions(user, content_id)
        assert {"ADD_CONTENT_DERIVATIVE", "ADD_CONTENT_COMMENT"} <= kinds


async def test_41_a_session_less_request_records_nothing(world: World) -> None:
    """Requirement 41. Contribution is open to *viewers*, not to the internet.

    Widening a rule to "anybody who may view" is only safe if the read itself is
    still gated, so this is the boundary that matters most in this step: no
    session, no contribution - and no thread either.
    """
    content_id, _ = await ready_to_publish(world)
    world.client.app.dependency_overrides.pop(  # type: ignore[attr-defined]
        __import__("meobot.api.deps", fromlist=["get_current_web_actor"]).get_current_web_actor,
        None,
    )
    try:
        assert (
            world.client.post(
                f"/api/pr/contents/{content_id}/derivatives",
                json={"derivative_type": "CUTDOWN", "label": "Ẩn danh", "location": CUT},
            ).status_code
            == 401
        )
        assert (
            world.client.post(
                f"/api/pr/contents/{content_id}/comments", json={"body": "Ẩn danh"}
            ).status_code
            == 401
        )
        assert world.client.get(f"/api/pr/contents/{content_id}/comments").status_code == 401
    finally:
        world.act_as(world.owner)
    assert await derivatives_of(world, content_id) == []


async def test_42_the_api_refuses_editing_somebody_elses_derivative(world: World) -> None:
    """Requirement 42. Over HTTP, because that is where a determined caller is.

    A 403 and an unchanged row - not a 200 with the change quietly dropped, which
    is the failure mode a client-side check produces when the server has none.
    """
    content_id, _ = await ready_to_publish(world)
    theirs = await add_derivative(world, content_id, actor=world.other)
    stranger = await outsider(world)

    world.act_as(stranger)
    response = world.client.patch(
        f"/api/pr/contents/{content_id}/derivatives/{theirs.id}",
        json={"label": "Đổi tên của người khác"},
    )
    assert response.status_code == 403, response.text

    deleted = world.client.delete(f"/api/pr/contents/{content_id}/derivatives/{theirs.id}")
    assert deleted.status_code == 403, deleted.text

    await world.session.refresh(theirs)
    assert theirs.label == "TikTok cut 25s"


async def test_43_44_the_api_refuses_editing_or_deleting_a_foreign_comment(
    world: World,
) -> None:
    """Requirements 43 and 44. Somebody else's words, over the wire.

    A colleague may read this thread and may add to it. Neither gets them a
    ``PATCH`` on a sentence they did not write, and the ``DELETE`` is refused for
    the same reason - they hold no moderation capability.

    An ``outsider`` rather than ``world.head`` on purpose: an ``ADMIN`` holds
    ``script.approve`` and therefore ``PR_CONTENT_CANCEL``, which **is** the
    moderator capability - so they may take this comment down, and requirement
    29-30 is where that is asserted. What no capability in this repository grants
    is rewording somebody else's words, which the loop in requirement 28 covers
    for management up to ``OWNER``.
    """
    content_id, _ = await ready_to_publish(world)
    said = await comment(world, content_id, actor=world.other)
    stranger = await outsider(world)

    world.act_as(stranger)
    patched = world.client.patch(
        f"/api/pr/contents/{content_id}/comments/{said.id}", json={"body": "Tôi viết lại"}
    )
    assert patched.status_code == 403, patched.text
    deleted = world.client.delete(f"/api/pr/contents/{content_id}/comments/{said.id}")
    assert deleted.status_code == 403, deleted.text

    await world.session.refresh(said)
    assert said.body == "Hook đoạn đầu hơi dài, cắt còn 3 giây nhé."
    assert said.deleted_at is None


async def test_45_a_comment_cannot_be_reached_through_another_items_url(
    world: World,
) -> None:
    """Requirement 45. The path names the aggregate, and it is checked.

    Two attacks in one shape. **Editing across items**: a comment id from piece A
    sent to piece B's URL, which without the check would let anybody who may view
    B edit a comment on A. And **replying across items**: a
    ``parent_comment_id`` from A on a ``POST`` to B, which would file an answer
    into a conversation the URL that fetched it would never show.

    A ``404`` for the first rather than a ``403``: the caller is not entitled to
    learn that the comment exists.
    """
    first, _ = await ready_to_publish(world)
    second = await make_content(world, owner=world.member, title="Bài khác")
    said = await comment(world, first, actor=world.other)

    world.act_as(world.other)
    crossed = world.client.patch(
        f"/api/pr/contents/{second}/comments/{said.id}", json={"body": "Qua bài khác"}
    )
    assert crossed.status_code == 404, crossed.text

    removed = world.client.delete(f"/api/pr/contents/{second}/comments/{said.id}")
    assert removed.status_code == 404, removed.text

    replied = world.client.post(
        f"/api/pr/contents/{second}/comments",
        json={"body": "Trả lời nhầm bài", "parent_comment_id": str(said.id)},
    )
    assert replied.status_code == 422, replied.text

    await world.session.refresh(said)
    assert said.body == "Hook đoạn đầu hơi dài, cắt còn 3 giây nhé."


async def test_46_a_comment_body_is_stored_and_returned_as_text(world: World) -> None:
    """Requirement 46. Markup is data here, and nothing on either side parses it.

    The server stores what was typed and sends it back unchanged - it does not
    escape, strip or rewrite, because the boundary that matters is the render and
    that is React putting it in a text node. What this asserts is that nothing in
    between *transformed* it: a server that helpfully stripped tags would make
    the panel's guarantee untestable and would mangle a comment about
    ``<Loading />``.
    """
    content_id, _ = await ready_to_publish(world)
    payload = '<script>alert("xin chào")</script> & <b>đậm</b>'
    world.act_as(world.other)
    response = world.client.post(f"/api/pr/contents/{content_id}/comments", json={"body": payload})
    assert response.status_code == 201, response.text
    assert response.json()["body"] == payload

    listed = world.client.get(f"/api/pr/contents/{content_id}/comments")
    assert listed.status_code == 200, listed.text
    assert listed.json()["items"][0]["body"] == payload
    # And nothing turned it into a second thing on the way in.
    stored = (
        (
            await world.session.execute(
                select(PrContentComment).where(PrContentComment.content_id == content_id)
            )
        )
        .scalars()
        .one()
    )
    assert stored.body == payload


# ===========================================================================
# The read model's own contract
# ===========================================================================


async def test_the_derivative_list_costs_a_fixed_number_of_queries(world: World) -> None:
    """No query per derivative, for the publication check or for the name.

    The per-row ``can_delete`` is the one that invites an N+1 - "has *this* one
    been published" is a natural question to ask per row - so the batched form is
    asserted the way the comment thread's is: by counting.
    """
    content_id, master = await ready_to_publish(world)
    for index in range(6):
        await add_derivative(world, content_id, actor=world.other, label=f"Cut {index}")
    await publish(world, content_id, submission=master)

    from sqlalchemy import event

    statements: list[str] = []

    def record(*args: object, **kwargs: object) -> None:
        statements.append("x")

    await world.session.flush()
    engine = world.session.get_bind().engine  # type: ignore[union-attr]
    event.listen(engine, "before_cursor_execute", record)
    try:
        seen = await views(world, content_id, actor=world.other)
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert len(seen) == 6
    # The content, the rows, the published set, the names, and the capability
    # and relationship lookups behind ``can_edit`` - all fixed, none per row.
    assert len(statements) <= 8, statements


async def test_a_derivative_recorded_now_reports_no_publication_history(
    world: World,
) -> None:
    """``is_published_output`` is the whole freeze rule, answered before the press.

    Sent so a form can disable location, type and lineage rather than offering
    them and rendering a 409 - and it counts **every** publication, a reversed
    one included, which is the same floor the permanent delete uses.
    """
    content_id, _ = await ready_to_publish(world)
    theirs = await add_derivative(world, content_id, actor=world.other)
    fresh = await views(world, content_id, actor=world.other)
    assert [view.is_published_output for view in fresh] == [False]

    publication = await publish(world, content_id, derivative=theirs, at=datetime.now(tz=UTC))
    await world.services.publications.reverse_publication(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        publication_id=publication.id,
    )
    seen = (await views(world, content_id, actor=world.other))[0]
    assert seen.is_published_output is True
    assert seen.can_delete is False


async def test_an_unknown_derivative_type_is_still_refused_for_a_stranger(
    world: World,
) -> None:
    """Widening *who* may write did not widen *what* may be written.

    The vocabulary, the label rule and the location rule are the domain's and are
    unchanged - a contributor gets the same refusals a producer always got.
    """
    content_id, _ = await ready_to_publish(world)
    with pytest.raises(PrValidationError):
        await world.services.content_assets.add_derivative(
            actor=world.actor(world.other),
            request_id=world.request_id,
            command=AddDerivativeCommand(
                content_id=content_id,
                derivative_type=PrContentDerivativeType.CUTDOWN,
                label="   ",
                location=CUT,
            ),
        )
    with pytest.raises(PrValidationError) as raised:
        await add_derivative(world, content_id, actor=world.other, location="javascript:alert(1)")
    assert raised.value.details["reason"] in {"unsafe_scheme", "unsupported_scheme"}


async def test_a_missing_content_item_is_not_found_for_either_feature(world: World) -> None:
    """Both new surfaces refuse an id that names nothing, rather than inventing it."""
    missing = uuid.uuid4()
    with pytest.raises(PrNotFoundError):
        await add_derivative(world, missing, actor=world.other)
    with pytest.raises(PrNotFoundError):
        await comment(world, missing, actor=world.other)
    with pytest.raises(PrNotFoundError):
        await thread(world, missing, actor=world.other)


async def test_replying_to_a_tombstone_is_refused(world: World) -> None:
    """Answering a comment that has been taken down is answering nothing.

    Refused rather than allowed-and-hidden: a reply under a tombstone that nobody
    can read the question of is a message with no context, and the person writing
    it should find out before they write it rather than after.
    """
    content_id, _ = await ready_to_publish(world)
    root = await comment(world, content_id, actor=world.other)
    await world.services.content_comments.delete_comment(
        actor=world.actor(world.other),
        request_id=world.request_id,
        content_id=content_id,
        comment_id=root.id,
    )
    with pytest.raises(PrValidationError) as raised:
        await comment(world, content_id, actor=world.member, body="Trả lời", parent=root)
    assert raised.value.details["reason"] == "comment_deleted"


async def test_deleting_twice_writes_one_audit_row(world: World) -> None:
    """A double-click does not produce two records of one removal."""
    content_id, _ = await ready_to_publish(world)
    said = await comment(world, content_id, actor=world.other)
    for _ in range(2):
        await world.services.content_comments.delete_comment(
            actor=world.actor(world.other),
            request_id=world.request_id,
            content_id=content_id,
            comment_id=said.id,
        )
    logged = await world.session.execute(
        select(func.count()).select_from(AuditLog).where(AuditLog.entity_id == str(said.id))
    )
    assert int(logged.scalar() or 0) == 1
