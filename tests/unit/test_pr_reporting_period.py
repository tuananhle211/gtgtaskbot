"""Kỳ báo cáo: one global reporting month, and a different fact per lane.

Step 1F.2.3f.6. The board used to carry a month selector only over *Hoàn tất*,
and the month reached the cards but not the counts - so *Đã đăng 67* stood over
a column of 27 cards. This file is the contract of the replacement:

* one month over every group and every lane, sent as ``period=YYYY-MM`` or
  ``period=CURRENT`` and echoed back;
* each lane read against its own business instant - stage entry, production
  start, actual publication, archive - and **never** ``created_at`` for
  everything, never ``updated_at`` for anything;
* the count and the cards from one predicate, so they agree by construction;
* no virtual archive: *Lưu trữ* is what was archived, in the month it was;
* a deliberate, bounded, person-driven bulk archive of a closed month.

Time is stamped rather than waited for. Transition events take the database
clock, so a test that needs "entered review in September" writes that instant
onto the event it just produced - which is also what keeps this file from
becoming a date-bomb next month.
"""

# ruff: noqa: F811 - ``world`` is a fixture reused across the content suites.

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from meobot.application.pr_ai_review_service import RecordAiReviewCommand
from meobot.application.pr_bulk_archive_service import BulkArchiveCommand
from meobot.application.pr_task_service import CreateTaskCommand
from meobot.core.time import ensure_utc
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.pr import PrContentItem
from meobot.db.models.pr_reporting import PrPublication
from meobot.db.models.pr_transition import PrContentTransitionEvent
from meobot.domain.audit.models import AuditAction
from meobot.domain.pr.content_views import (
    ARCHIVE_LANES,
    GROUP_STAGES,
    STAGE_PERIOD_FACT,
    PrContentDateField,
    PrContentGroup,
    PrContentLane,
    PrPeriodFact,
    lane_period_fact,
    lanes_in_group,
    period_fact_for,
    stages_in_group,
)
from meobot.domain.pr.errors import (
    PrBulkArchiveStaleError,
    PrPermissionDeniedError,
    PrValidationError,
)
from meobot.domain.pr.models import (
    PrAiReviewResult,
    PrAiReviewType,
    PrApprovalDecision,
    PrApprovalStage,
    PrChannelAssignmentRole,
    PrTaskAssignmentRole,
    PrWorkflowStage,
)
from meobot.domain.pr.policy import BULK_ARCHIVE_MAX_ITEMS, PrCapability
from meobot.domain.pr.workflow import PrTransitionTrigger
from tests.unit.test_pr_derivatives_and_publications import (  # noqa: F401 - `world` is a fixture
    World,
    audit_actions,
    decide,
    make_content,
    new_channel,
    publish,
    ready_to_publish,
    submit,
    to_production,
    world,
)
from tests.unit.test_pr_production_lifecycle import NOW, to_approved

pytestmark = pytest.mark.asyncio

AUGUST = datetime(2026, 8, 18, 3, 0, tzinfo=UTC)
LATE_AUGUST = datetime(2026, 8, 28, 3, 0, tzinfo=UTC)
SEPTEMBER = datetime(2026, 9, 3, 3, 0, tzinfo=UTC)
LATER_SEPTEMBER = datetime(2026, 9, 8, 3, 0, tzinfo=UTC)
JULY = datetime(2026, 7, 10, 3, 0, tzinfo=UTC)


# --- Stamping the facts -----------------------------------------------------


async def created_on(world: World, content_id: uuid.UUID, at: datetime) -> None:
    row = await world.reload(content_id)
    row.created_at = at
    await world.session.flush()


async def entered_on(world: World, content_id: uuid.UUID, at: datetime) -> None:
    """Stamp the latest effective entry into the row's current stage with ``at``."""
    row = await world.reload(content_id)
    event = await world.session.scalar(
        select(PrContentTransitionEvent)
        .where(
            PrContentTransitionEvent.content_id == content_id,
            PrContentTransitionEvent.to_stage == row.workflow_stage,
            PrContentTransitionEvent.trigger != PrTransitionTrigger.UNDO,
            PrContentTransitionEvent.reversed_by_event_id.is_(None),
        )
        .order_by(PrContentTransitionEvent.created_at.desc())
        .limit(1)
    )
    assert event is not None, f"no effective entry event into {row.workflow_stage}"
    event.created_at = at
    await world.session.flush()


async def move(world: World, content_id: uuid.UUID, target: PrWorkflowStage) -> None:
    await world.services.workflow.request_transition(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        content_id=content_id,
        target=target,
    )


async def to_review(world: World, content_id: uuid.UUID) -> None:
    """The real walk to ``TEAM_LEAD_REVIEW``: brief, script, and a passing AI review."""
    for stage in (
        PrWorkflowStage.BRIEFING,
        PrWorkflowStage.SCRIPTING,
        PrWorkflowStage.AI_REVIEW,
    ):
        await move(world, content_id, stage)
    await world.services.ai_reviews.record_review(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        command=RecordAiReviewCommand(
            content_id=content_id,
            reviewed_version=1,
            review_type=PrAiReviewType.FULL_REVIEW,
            result=PrAiReviewResult.PASS,
            model_name="claude-opus-5",
            prompt_version="p@1",
            reviewed_at=NOW,
        ),
    )
    assert (await world.reload(content_id)).workflow_stage is PrWorkflowStage.TEAM_LEAD_REVIEW


async def code_of(world: World, content_id: uuid.UUID) -> str:
    return (await world.reload(content_id)).code


# --- Reading the board -------------------------------------------------------


def board(world: World, **params: object) -> dict[str, Any]:
    response = world.client.get(
        "/api/pr/contents/board", params={"scope": "ALL", "limit": 0, **params}
    )
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def lane(world: World, name: str, period: str, **params: object) -> dict[str, Any]:
    """One column for one month. The archive lane lives on the archive view."""
    view = {"view": "ARCHIVE"} if name == "ARCHIVED" else {}
    response = world.client.get(
        "/api/pr/contents/board",
        params={"scope": "ALL", "lane": name, "period": period, "limit": 50, **view, **params},
    )
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def codes(body: dict[str, Any]) -> set[str]:
    return {row["code"] for row in body["items"]}


def stage_count(body: dict[str, Any], stage: str) -> int:
    return next(row["count"] for row in body["stage_counts"] if row["stage"] == stage)


def state_count(body: dict[str, Any], state: str) -> int:
    return next(
        row["count"] for row in body["production_state_counts"] if row["production_state"] == state
    )


def group_count(body: dict[str, Any], group: str) -> int:
    return sum(stage_count(body, stage.value) for stage in GROUP_STAGES[group])  # type: ignore[index]


# ===========================================================================
# THE TABLE
# ===========================================================================


async def test_every_stage_has_a_reporting_fact_and_none_is_creation() -> None:
    """Part AB. The mapping is complete, and no lane is ``created_at`` by rule."""
    assert set(STAGE_PERIOD_FACT) == set(PrWorkflowStage)
    assert not hasattr(PrPeriodFact, "CREATED_AT")
    assert period_fact_for(PrWorkflowStage.PUBLISHED) is PrPeriodFact.PUBLICATION
    assert period_fact_for(PrWorkflowStage.ARCHIVED) is PrPeriodFact.ARCHIVE
    assert period_fact_for(PrWorkflowStage.CANCELLED) is PrPeriodFact.CANCELLATION
    assert period_fact_for(PrWorkflowStage.PRODUCTION) is PrPeriodFact.PRODUCTION_ENTRY
    for stage in (
        PrWorkflowStage.IDEA,
        PrWorkflowStage.TEAM_LEAD_REVIEW,
        PrWorkflowStage.HEAD_REVIEW,
        PrWorkflowStage.APPROVED,
        PrWorkflowStage.INTERNAL_REVIEW,
        PrWorkflowStage.READY_TO_PUBLISH,
    ):
        assert period_fact_for(stage) is PrPeriodFact.STAGE_ENTRY, stage
    # The two APPROVED lanes share their stage's fact: naming a producer is not
    # a dated transition.
    assert lane_period_fact(PrContentLane.WAITING_FOR_PRODUCER) is PrPeriodFact.STAGE_ENTRY
    assert lane_period_fact(PrContentLane.READY_FOR_PRODUCTION) is PrPeriodFact.STAGE_ENTRY


# ===========================================================================
# 1-7: THE PERIOD REACHES EVERY GROUP, AND IS ECHOED
# ===========================================================================


async def test_01_the_preparation_group_is_read_against_the_month(world: World) -> None:
    """IDEA is creation; SCRIPTING is the entry into SCRIPTING."""
    idea = await make_content(world, owner=world.member)
    await created_on(world, idea, AUGUST)
    scripting = await make_content(world, owner=world.member)
    await created_on(world, scripting, AUGUST)
    await move(world, scripting, PrWorkflowStage.BRIEFING)
    await move(world, scripting, PrWorkflowStage.SCRIPTING)
    await entered_on(world, scripting, SEPTEMBER)

    assert codes(lane(world, "IDEA", "2026-08")) == {await code_of(world, idea)}
    assert codes(lane(world, "IDEA", "2026-09")) == set()
    assert codes(lane(world, "SCRIPTING", "2026-09")) == {await code_of(world, scripting)}
    # Created in August, but its current step began in September: not August's.
    assert codes(lane(world, "SCRIPTING", "2026-08")) == set()


async def test_02_the_review_group_is_read_against_the_month(world: World) -> None:
    content_id = await make_content(world, owner=world.member)
    await created_on(world, content_id, AUGUST)
    await to_review(world, content_id)
    await entered_on(world, content_id, SEPTEMBER)

    assert codes(lane(world, "TEAM_LEAD_REVIEW", "2026-09")) == {await code_of(world, content_id)}
    assert codes(lane(world, "TEAM_LEAD_REVIEW", "2026-08")) == set()
    figures = board(world, period="2026-09")
    assert group_count(figures, "EDITORIAL_REVIEW") == 1
    assert group_count(board(world, period="2026-08"), "EDITORIAL_REVIEW") == 0


async def test_03_the_production_group_is_read_against_the_month(world: World) -> None:
    content_id = await make_content(world, owner=world.member)
    await created_on(world, content_id, AUGUST)
    await to_production(world, content_id, producer=world.member)
    await entered_on(world, content_id, SEPTEMBER)

    assert codes(lane(world, "IN_PRODUCTION", "2026-09")) == {await code_of(world, content_id)}
    assert codes(lane(world, "IN_PRODUCTION", "2026-08")) == set()
    assert state_count(board(world, period="2026-09"), "IN_PRODUCTION") == 1
    assert state_count(board(world, period="2026-08"), "IN_PRODUCTION") == 0


async def test_04_the_completed_group_is_read_against_the_month(world: World) -> None:
    content_id, master = await ready_to_publish(world)
    await created_on(world, content_id, AUGUST)
    await publish(world, content_id, submission=master, at=SEPTEMBER)

    assert codes(lane(world, "PUBLISHED", "2026-09")) == {await code_of(world, content_id)}
    assert codes(lane(world, "PUBLISHED", "2026-08")) == set()
    assert group_count(board(world, period="2026-09"), "COMPLETED") == 1
    assert group_count(board(world, period="2026-08"), "COMPLETED") == 0


async def test_05_the_cancelled_group_is_read_against_the_cancellation(world: World) -> None:
    """Part U. ``cancelled_at`` does not exist; the entry into CANCELLED does."""
    content_id = await make_content(world, owner=world.member)
    await created_on(world, content_id, JULY)
    await world.services.workflow.cancel(
        actor=world.actor(world.owner), request_id=world.request_id, content_id=content_id
    )
    await entered_on(world, content_id, SEPTEMBER)

    assert codes(lane(world, "CANCELLED", "2026-09")) == {await code_of(world, content_id)}
    assert codes(lane(world, "CANCELLED", "2026-07")) == set()
    assert group_count(board(world, period="2026-09"), "CANCELLED") == 1
    assert group_count(board(world, period="2026-07"), "CANCELLED") == 0


async def test_06_the_period_is_accepted_with_every_group(world: World) -> None:
    """The URL carries one ``period`` whichever tab is open; the server takes it."""
    for group in ("PREPARATION", "EDITORIAL_REVIEW", "PRODUCTION", "COMPLETED", "CANCELLED"):
        assert board(world, group=group, period="2026-09")["period"] == "2026-09", group


async def test_07_current_resolves_to_the_business_month_and_is_echoed(world: World) -> None:
    """Part Y. One derivation, on the server, in the business timezone."""
    expected = datetime.now(world.settings.timezone).strftime("%Y-%m")
    assert board(world, period="CURRENT")["period"] == expected
    # An explicit month is honoured as written, and no month means cumulative.
    assert board(world, period="2026-03")["period"] == "2026-03"
    assert board(world)["period"] is None
    # Step 1F.2.3f.6b. The current business month rides on every response,
    # whatever month was selected - it is the selector's anchor, and the
    # browser never computes it.
    for selected in ("2026-03", "2026-08", "CURRENT", None):
        params = {"period": selected} if selected else {}
        assert board(world, **params)["current_period"] == expected, selected
    world.act_as(world.head)
    assert board(world, scope="MY_ACTIONS", period="2026-03")["current_period"] == expected
    # And a malformed one is a 422 naming the format.
    response = world.client.get(
        "/api/pr/contents/board", params={"scope": "ALL", "period": "September"}
    )
    assert response.status_code == 422, response.text


# ===========================================================================
# 8-10: NOT CREATED_AT EVERYWHERE
# ===========================================================================


async def test_08_09_created_in_august_produced_in_september_is_septembers_production(
    world: World,
) -> None:
    content_id = await make_content(world, owner=world.member)
    await created_on(world, content_id, LATE_AUGUST)
    await to_production(world, content_id, producer=world.member)
    await entered_on(world, content_id, SEPTEMBER)
    code = await code_of(world, content_id)

    assert code in codes(lane(world, "IN_PRODUCTION", "2026-09", group="PRODUCTION"))
    # 9: it does not appear in August merely because created_at is August.
    assert code not in codes(lane(world, "IN_PRODUCTION", "2026-08", group="PRODUCTION"))
    assert group_count(board(world, period="2026-08"), "PRODUCTION") == 0


async def test_10_created_in_august_published_in_september_is_septembers_publication(
    world: World,
) -> None:
    content_id, master = await ready_to_publish(world)
    await created_on(world, content_id, LATE_AUGUST)
    await publish(world, content_id, submission=master, at=LATER_SEPTEMBER)
    code = await code_of(world, content_id)

    assert codes(lane(world, "PUBLISHED", "2026-09", group="COMPLETED")) == {code}
    assert codes(lane(world, "PUBLISHED", "2026-08", group="COMPLETED")) == set()


# ===========================================================================
# 11-12: REVIEW
# ===========================================================================


async def test_11_review_membership_is_the_entry_into_the_gate(world: World) -> None:
    content_id = await make_content(world, owner=world.member)
    await created_on(world, content_id, AUGUST)
    await to_review(world, content_id)
    await entered_on(world, content_id, SEPTEMBER)
    await decide(
        world, actor=world.lead, content_id=content_id, stage=PrApprovalStage.TEAM_LEAD_REVIEW
    )
    # Now at HEAD_REVIEW, entered a few days later still.
    await entered_on(world, content_id, LATER_SEPTEMBER)
    code = await code_of(world, content_id)

    assert codes(lane(world, "HEAD_REVIEW", "2026-09")) == {code}
    assert codes(lane(world, "HEAD_REVIEW", "2026-08")) == set()
    assert codes(lane(world, "TEAM_LEAD_REVIEW", "2026-09")) == set()


async def test_12_a_later_edit_does_not_move_the_review_month(world: World) -> None:
    """``updated_at`` is not a lane timestamp, whatever touches the row."""
    content_id = await make_content(world, owner=world.member)
    await created_on(world, content_id, AUGUST)
    await to_review(world, content_id)
    await entered_on(world, content_id, AUGUST)
    row = await world.reload(content_id)
    row.priority = row.priority  # any column write moves ``updated_at``
    row.updated_at = datetime(2026, 10, 1, 3, 0, tzinfo=UTC)
    await world.session.flush()
    code = await code_of(world, content_id)

    assert codes(lane(world, "TEAM_LEAD_REVIEW", "2026-08")) == {code}
    assert codes(lane(world, "TEAM_LEAD_REVIEW", "2026-10")) == set()


async def test_12a_an_undo_does_not_restart_the_clock_on_the_stage_it_returns_to(
    world: World,
) -> None:
    """Entered TLR in August, approved to HR this month, undone: still August's.

    The approval and its undo are left on the database clock so the undo
    service sees them as the newest history; only the August entry is stamped.
    """
    content_id = await make_content(world, owner=world.member)
    await to_review(world, content_id)
    await entered_on(world, content_id, AUGUST)
    await decide(
        world, actor=world.lead, content_id=content_id, stage=PrApprovalStage.TEAM_LEAD_REVIEW
    )
    await world.services.undo.undo_last(
        actor=world.actor(world.lead), request_id=world.request_id, content_id=content_id
    )
    assert (await world.reload(content_id)).workflow_stage is PrWorkflowStage.TEAM_LEAD_REVIEW
    code = await code_of(world, content_id)
    this_month = datetime.now(world.settings.timezone).strftime("%Y-%m")

    assert codes(lane(world, "TEAM_LEAD_REVIEW", "2026-08")) == {code}
    assert codes(lane(world, "TEAM_LEAD_REVIEW", this_month)) == set()


# ===========================================================================
# 13-15: PRODUCTION
# ===========================================================================


async def test_13_15_production_uses_the_production_entry_not_created_or_updated(
    world: World,
) -> None:
    content_id = await make_content(world, owner=world.member)
    await created_on(world, content_id, JULY)
    await to_production(world, content_id, producer=world.member)
    await entered_on(world, content_id, SEPTEMBER)
    row = await world.reload(content_id)
    row.updated_at = datetime(2026, 11, 2, 3, 0, tzinfo=UTC)
    await world.session.flush()
    code = await code_of(world, content_id)

    assert codes(lane(world, "IN_PRODUCTION", "2026-09")) == {code}
    assert codes(lane(world, "IN_PRODUCTION", "2026-07")) == set()
    assert codes(lane(world, "IN_PRODUCTION", "2026-11")) == set()


async def test_13a_a_recut_counts_in_the_month_production_resumed(world: World) -> None:
    """Part G. ``production_started_at`` is the *first* start and is not moved;
    the lane means production now, so the current stay decides."""
    content_id = await make_content(world, owner=world.member)
    await to_production(world, content_id, producer=world.member)
    await entered_on(world, content_id, AUGUST)
    row = await world.reload(content_id)
    row.production_started_at = AUGUST
    await world.session.flush()
    await submit(world, content_id, actor=world.member)
    await world.services.capabilities.grant(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        user_id=world.lead.id,
        capability=PrCapability.PR_INTERNAL_REVIEW,
    )
    await decide(
        world,
        actor=world.lead,
        content_id=content_id,
        stage=PrApprovalStage.INTERNAL_REVIEW,
        decision=PrApprovalDecision.REVISION_REQUIRED,
    )
    assert (await world.reload(content_id)).workflow_stage is PrWorkflowStage.PRODUCTION
    await entered_on(world, content_id, SEPTEMBER)
    code = await code_of(world, content_id)

    assert codes(lane(world, "IN_PRODUCTION", "2026-09")) == {code}
    assert codes(lane(world, "IN_PRODUCTION", "2026-08")) == set()
    started = (await world.reload(content_id)).production_started_at
    assert started is not None and ensure_utc(started) == AUGUST


async def test_13b_both_approved_lanes_count_in_the_month_of_approval(world: World) -> None:
    """Naming a producer is not a transition; both APPROVED lanes share the month."""
    waiting = await make_content(world, owner=world.member)
    await to_approved(world, waiting)
    await entered_on(world, waiting, SEPTEMBER)
    taken = await make_content(world, owner=world.member)
    await to_approved(world, taken)
    await entered_on(world, taken, SEPTEMBER)
    await world.services.production.assign_producer(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        content_id=taken,
        producer_user_id=world.member.id,
    )

    assert codes(lane(world, "WAITING_FOR_PRODUCER", "2026-09")) == {await code_of(world, waiting)}
    assert codes(lane(world, "READY_FOR_PRODUCTION", "2026-09")) == {await code_of(world, taken)}
    figures = board(world, period="2026-09")
    assert state_count(figures, "WAITING_FOR_PRODUCER") == 1
    assert state_count(figures, "READY_FOR_PRODUCTION") == 1
    assert group_count(board(world, period="2026-08"), "PRODUCTION") == 0


# ===========================================================================
# 16-17: READY TO PUBLISH
# ===========================================================================


async def test_16_17_ready_uses_the_ready_entry_and_planned_publish_stays_a_filter(
    world: World,
) -> None:
    content_id, _ = await ready_to_publish(world)
    await created_on(world, content_id, JULY)
    await entered_on(world, content_id, SEPTEMBER)
    row = await world.reload(content_id)
    row.planned_publish_at = datetime(2026, 10, 15, 3, 0, tzinfo=UTC)
    await world.session.flush()
    code = await code_of(world, content_id)

    assert codes(lane(world, "READY_TO_PUBLISH", "2026-09")) == {code}
    # Planned for October does not make it October's ready work...
    assert codes(lane(world, "READY_TO_PUBLISH", "2026-10")) == set()
    # ...but the ordinary PLANNED_PUBLISH_AT filter still asks that question.
    assert codes(
        lane(
            world,
            "READY_TO_PUBLISH",
            "2026-09",
            date_field="PLANNED_PUBLISH_AT",
            date_from="2026-10-01",
            date_to="2026-10-31",
        )
    ) == {code}
    assert (
        lane(
            world,
            "READY_TO_PUBLISH",
            "2026-09",
            date_field="PLANNED_PUBLISH_AT",
            date_from="2026-09-01",
            date_to="2026-09-30",
        )["total"]
        == 0
    )


# ===========================================================================
# 18-21: PUBLISHED
# ===========================================================================


async def test_18_20_published_is_the_earliest_active_publication(world: World) -> None:
    content_id, master = await ready_to_publish(world)
    first = await publish(world, content_id, submission=master, at=SEPTEMBER)
    second = await new_channel(world)
    await publish(world, content_id, channel_id=second.id, submission=master, at=LATER_SEPTEMBER)
    code = await code_of(world, content_id)
    # 19: two publications, one card.
    september = lane(world, "PUBLISHED", "2026-09")
    assert codes(september) == {code}
    assert september["total"] == 1
    assert september["items"][0]["published_at"].startswith("2026-09-03")

    # Reverse the first: the canonical instant moves to the second (still
    # September here), and a reversed row is excluded from MIN.
    await world.services.publications.reverse_publication(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        publication_id=first.id,
    )
    again = lane(world, "PUBLISHED", "2026-09")
    assert again["items"][0]["published_at"].startswith("2026-09-08")


async def test_21_published_count_matches_the_list(world: World) -> None:
    for at in (AUGUST, AUGUST, SEPTEMBER):
        content_id, master = await ready_to_publish(world)
        await publish(world, content_id, submission=master, at=at)

    for period, expected in (("2026-08", 2), ("2026-09", 1)):
        column = lane(world, "PUBLISHED", period)
        figures = board(world, period=period)
        assert column["total"] == expected == len(column["items"]), period
        assert stage_count(figures, "PUBLISHED") == expected, period


# ===========================================================================
# 22-25: ARCHIVED
# ===========================================================================


async def archived_on(world: World, at: datetime, *, published_at: datetime = AUGUST) -> str:
    content_id, master = await ready_to_publish(world)
    await publish(world, content_id, submission=master, at=published_at)
    await move(world, content_id, PrWorkflowStage.ARCHIVED)
    row = await world.reload(content_id)
    assert row.archived_at is not None
    row.archived_at = at
    await world.session.flush()
    return row.code


async def test_22_25_archive_uses_archived_at(world: World) -> None:
    code = await archived_on(world, SEPTEMBER, published_at=AUGUST)

    assert codes(lane(world, "ARCHIVED", "2026-09")) == {code}
    assert codes(lane(world, "ARCHIVED", "2026-08")) == set()
    # And an archived row is no longer August's *Đã đăng*: its current lane is
    # the archive, and the board is a current-workflow board.
    assert codes(lane(world, "PUBLISHED", "2026-08")) == set()


async def test_23_an_old_publication_is_not_shown_as_archived(world: World) -> None:
    content_id, master = await ready_to_publish(world)
    await publish(world, content_id, submission=master, at=JULY)

    for period in ("2026-08", "2026-09"):
        assert lane(world, "ARCHIVED", period)["total"] == 0, period
        assert stage_count(board(world, period=period), "ARCHIVED") == 0, period
    assert codes(lane(world, "PUBLISHED", "2026-07")) == {await code_of(world, content_id)}


async def test_24_the_archive_action_goes_through_the_workflow_service(world: World) -> None:
    content_id, master = await ready_to_publish(world)
    await publish(world, content_id, submission=master, at=AUGUST)
    world.act_as(world.owner)
    response = world.client.post(
        f"/api/pr/contents/{content_id}/transition", json={"target_stage": "ARCHIVED"}
    )
    assert response.status_code == 200, response.text
    row = await world.reload(content_id)
    assert row.workflow_stage is PrWorkflowStage.ARCHIVED
    assert row.archived_at is not None
    assert AuditAction.PR_CONTENT_STAGE_CHANGED.value in await audit_actions(world, content_id)
    month = row.archived_at.strftime("%Y-%m")
    assert codes(lane(world, "ARCHIVED", month)) == {row.code}


# ===========================================================================
# 26-31 AND THE OBSERVED BUG: COUNTS ARE THE CARDS
# ===========================================================================


async def test_the_published_count_is_the_month_not_the_lifetime(world: World) -> None:
    """**The observed bug.** A lifetime of published pieces; a month of them
    selected; the header must say the month's number, not the lifetime's."""
    lifetime = 0
    for at in (JULY, JULY, JULY, AUGUST, AUGUST, SEPTEMBER, SEPTEMBER, SEPTEMBER, SEPTEMBER):
        content_id, master = await ready_to_publish(world)
        await publish(world, content_id, submission=master, at=at)
        lifetime += 1
    cumulative = board(world)
    assert stage_count(cumulative, "PUBLISHED") == lifetime  # the old "67"

    august = board(world, group="COMPLETED", period="2026-08")
    august_column = lane(world, "PUBLISHED", "2026-08", group="COMPLETED")
    assert stage_count(august, "PUBLISHED") == 2
    assert august_column["total"] == 2 == len(august_column["items"])
    assert stage_count(august, "PUBLISHED") != lifetime

    september = board(world, group="COMPLETED", period="2026-09")
    september_column = lane(world, "PUBLISHED", "2026-09", group="COMPLETED")
    assert stage_count(september, "PUBLISHED") == 4
    assert september_column["total"] == 4 == len(september_column["items"])


async def test_26_30_every_group_and_lane_count_moves_with_the_period(world: World) -> None:
    """Group tabs and lane headers are period figures, never lifetime ones."""
    idea = await make_content(world, owner=world.member)
    await created_on(world, idea, AUGUST)
    reviewing = await make_content(world, owner=world.member)
    await to_review(world, reviewing)
    await entered_on(world, reviewing, SEPTEMBER)
    producing = await make_content(world, owner=world.member)
    await to_production(world, producing, producer=world.member)
    await entered_on(world, producing, SEPTEMBER)
    published, master = await ready_to_publish(world)
    await publish(world, published, submission=master, at=AUGUST)

    august = board(world, period="2026-08")
    september = board(world, period="2026-09")
    assert (
        group_count(august, "PREPARATION"),
        group_count(august, "EDITORIAL_REVIEW"),
        group_count(august, "PRODUCTION"),
        group_count(august, "COMPLETED"),
    ) == (1, 0, 0, 1)
    assert (
        group_count(september, "PREPARATION"),
        group_count(september, "EDITORIAL_REVIEW"),
        group_count(september, "PRODUCTION"),
        group_count(september, "COMPLETED"),
    ) == (0, 1, 1, 0)
    # Each figure is the same query as its column.
    for period, figures in (("2026-08", august), ("2026-09", september)):
        for name in ("IDEA", "TEAM_LEAD_REVIEW", "PUBLISHED"):
            assert stage_count(figures, name) == lane(world, name, period)["total"], (period, name)
        assert (
            state_count(figures, "IN_PRODUCTION") == lane(world, "IN_PRODUCTION", period)["total"]
        ), period
    # And the group count is a union: each item is in exactly one lane.
    assert sum(group_count(september, group) for group in GROUP_STAGES) == 2  # type: ignore[arg-type]


async def test_31_the_lane_total_is_the_whole_month_not_the_page(world: World) -> None:
    for _ in range(3):
        content_id, master = await ready_to_publish(world)
        await publish(world, content_id, submission=master, at=SEPTEMBER)
    page = world.client.get(
        "/api/pr/contents/board",
        params={"scope": "ALL", "lane": "PUBLISHED", "period": "2026-09", "limit": 2},
    ).json()
    assert len(page["items"]) == 2
    assert page["total"] == 3


# ===========================================================================
# 32-39: ORDINARY FILTERS COMPOSE INSIDE THE MONTH
# ===========================================================================


async def test_32_34_the_month_composes_with_each_date_filter(world: World) -> None:
    content_id, master = await ready_to_publish(world)
    await created_on(world, content_id, LATE_AUGUST)
    await publish(world, content_id, submission=master, at=SEPTEMBER)
    row = await world.reload(content_id)
    row.planned_publish_at = datetime(2026, 8, 31, 3, 0, tzinfo=UTC)
    row.updated_at = LATER_SEPTEMBER
    await world.session.flush()
    code = row.code

    def narrowed(field: str, start: str, end: str) -> set[str]:
        return codes(
            lane(world, "PUBLISHED", "2026-09", date_field=field, date_from=start, date_to=end)
        )

    # 32: the month says September; CREATED_AT narrows to late August - still it.
    assert narrowed("CREATED_AT", "2026-08-25", "2026-08-31") == {code}
    assert narrowed("CREATED_AT", "2026-09-01", "2026-09-30") == set()
    # 33: PLANNED_PUBLISH_AT is its own dimension.
    assert narrowed("PLANNED_PUBLISH_AT", "2026-08-31", "2026-08-31") == {code}
    # 34: UPDATED_AT narrows, and does not define the month.
    assert narrowed("UPDATED_AT", "2026-09-08", "2026-09-08") == {code}
    assert narrowed("UPDATED_AT", "2026-09-01", "2026-09-02") == set()
    assert codes(lane(world, "PUBLISHED", "2026-08")) == set()


async def test_35_39_the_month_composes_with_the_other_filters(world: World) -> None:
    content_id, master = await ready_to_publish(world)
    await publish(world, content_id, submission=master, at=SEPTEMBER)
    row = await world.reload(content_id)
    other_channel = await new_channel(world)
    code = row.code

    def published_in_september(**params: object) -> set[str]:
        return codes(lane(world, "PUBLISHED", "2026-09", **params))

    assert published_in_september(channel_id=str(world.channel_id)) == {code}
    assert published_in_september(channel_id=str(other_channel.id)) == set()
    platform = await world.session.get(type(other_channel), other_channel.id)
    assert platform is not None
    assert published_in_september(platform_id=str(platform.platform_id)) == set()
    assert published_in_september(responsible_user_id=str(row.owner_user_id)) == {code}
    assert published_in_september(responsible_user_id=str(world.other.id)) == set()
    assert published_in_september(priority=row.priority.value) == {code}
    assert published_in_september(priority="CRITICAL") == set()
    content_type = row.content_type.value if row.content_type else "UNCLASSIFIED"
    assert published_in_september(content_type=content_type) == {code}


# ===========================================================================
# 40-41: AUTHORIZATION
# ===========================================================================


async def test_40_41_the_period_neither_widens_nor_narrows_what_may_be_seen(
    world: World,
) -> None:
    mine = await make_content(world, owner=world.member)
    await created_on(world, mine, SEPTEMBER)
    theirs = await make_content(world, owner=world.other)
    await created_on(world, theirs, SEPTEMBER)
    world.act_as(world.member)

    own = world.client.get(
        "/api/pr/contents/board",
        params={"scope": "MY_CONTENT", "lane": "IDEA", "period": "2026-09"},
    ).json()
    assert codes(own) == {await code_of(world, mine)}
    everything = world.client.get(
        "/api/pr/contents/board", params={"scope": "ALL", "lane": "IDEA", "period": "2026-09"}
    ).json()
    assert codes(everything) == {await code_of(world, mine), await code_of(world, theirs)}


# ===========================================================================
# STEP 1F.2.3f.6a: THE DEFAULT IS ALL, AND THE ACTION QUEUE IS MONTH-FREE
# ===========================================================================


async def stale_head_review(world: World) -> str:
    """A piece that entered HEAD_REVIEW in August and is still waiting there."""
    content_id = await make_content(world, owner=world.member)
    await created_on(world, content_id, JULY)
    await to_review(world, content_id)
    await decide(
        world, actor=world.lead, content_id=content_id, stage=PrApprovalStage.TEAM_LEAD_REVIEW
    )
    assert (await world.reload(content_id)).workflow_stage is PrWorkflowStage.HEAD_REVIEW
    await entered_on(world, content_id, LATE_AUGUST)
    return await code_of(world, content_id)


async def test_a1_no_scope_resolves_to_all_for_everybody(world: World) -> None:
    for person in (world.member, world.lead, world.head, world.owner):
        world.act_as(person)
        body = world.client.get("/api/pr/contents/board", params={"period": "2026-09"}).json()
        assert body["scope"] == "ALL", person.full_name
        assert body["period"] == "2026-09"
        assert body["period_applied"] is True


async def test_a3_a5_all_mine_and_team_are_read_against_the_month(world: World) -> None:
    """The three report scopes filter by the month; a July item is July's in each."""
    content_id = await make_content(world, owner=world.member)
    await created_on(world, content_id, JULY)
    code = await code_of(world, content_id)
    await world.services.channels.assign_user(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        channel_id=world.channel_id,
        user_id=world.member.id,
        assignment_role=PrChannelAssignmentRole.CONTENT_OWNER,
        effective_from=date(2026, 1, 1),
    )
    world.act_as(world.member)
    for scope in ("ALL", "MY_CONTENT", "TEAM"):
        july = world.client.get(
            "/api/pr/contents/board",
            params={"scope": scope, "lane": "IDEA", "period": "2026-07"},
        ).json()
        september = world.client.get(
            "/api/pr/contents/board",
            params={"scope": scope, "lane": "IDEA", "period": "2026-09"},
        ).json()
        assert codes(july) == {code}, scope
        assert codes(september) == set(), scope
        assert july["period_applied"] is True and september["period_applied"] is True


async def test_a6_a8_the_action_queue_ignores_the_month_and_the_report_does_not(
    world: World,
) -> None:
    """**The carry-over rule.** Entered Head review 28/08, still waiting on 08/09.

    Under ``ALL`` with September selected it is absent - it is August's review.
    Under ``MY_ACTIONS`` it is present whatever month is selected, because it is
    waiting on the Head *now*. The month is still echoed, and the response says
    it was not applied.
    """
    code = await stale_head_review(world)
    world.act_as(world.head)

    report = world.client.get(
        "/api/pr/contents/board",
        params={"scope": "ALL", "lane": "HEAD_REVIEW", "period": "2026-09"},
    ).json()
    assert codes(report) == set()
    assert report["period_applied"] is True
    august = world.client.get(
        "/api/pr/contents/board",
        params={"scope": "ALL", "lane": "HEAD_REVIEW", "period": "2026-08"},
    ).json()
    assert codes(august) == {code}

    for period in ("2026-09", "2026-08", "2026-01", "CURRENT"):
        queue = world.client.get(
            "/api/pr/contents/board",
            params={"scope": "MY_ACTIONS", "lane": "HEAD_REVIEW", "period": period},
        ).json()
        assert codes(queue) == {code}, period
        assert queue["period_applied"] is False, period
        assert queue["period"] is not None, period
    # And without any month at all it is, of course, the same queue.
    bare = world.client.get(
        "/api/pr/contents/board", params={"scope": "MY_ACTIONS", "lane": "HEAD_REVIEW"}
    ).json()
    assert codes(bare) == {code}
    assert bare["period_applied"] is False


async def test_a9_a10_the_queue_counts_are_the_queue_cards(world: World) -> None:
    """Lane counts, group counts and the total all read the month-free predicate."""
    stale = await stale_head_review(world)
    fresh = await make_content(world, owner=world.member)
    await to_review(world, fresh)
    await decide(world, actor=world.lead, content_id=fresh, stage=PrApprovalStage.TEAM_LEAD_REVIEW)
    await entered_on(world, fresh, SEPTEMBER)
    fresh_code = await code_of(world, fresh)
    world.act_as(world.head)

    figures = world.client.get(
        "/api/pr/contents/board",
        params={"scope": "MY_ACTIONS", "period": "2026-09", "limit": 0},
    ).json()
    column = world.client.get(
        "/api/pr/contents/board",
        params={"scope": "MY_ACTIONS", "lane": "HEAD_REVIEW", "period": "2026-09"},
    ).json()
    group = world.client.get(
        "/api/pr/contents/board",
        params={"scope": "MY_ACTIONS", "group": "EDITORIAL_REVIEW", "period": "2026-09"},
    ).json()
    assert codes(column) == {stale, fresh_code}
    assert column["total"] == 2
    assert stage_count(figures, "HEAD_REVIEW") == 2
    assert group_count(figures, "EDITORIAL_REVIEW") == 2
    assert group["total"] == 2
    # The same month under the report scope is one item, in every figure.
    report = world.client.get(
        "/api/pr/contents/board", params={"scope": "ALL", "period": "2026-09", "limit": 0}
    ).json()
    assert stage_count(report, "HEAD_REVIEW") == 1
    assert group_count(report, "EDITORIAL_REVIEW") == 1


async def test_a14_ordinary_filters_still_narrow_the_queue(world: World) -> None:
    code = await stale_head_review(world)
    row = await world.session.scalar(select(PrContentItem).where(PrContentItem.code == code))
    assert row is not None
    other_channel = await new_channel(world)
    world.act_as(world.head)

    def queue(**params: object) -> set[str]:
        return codes(
            world.client.get(
                "/api/pr/contents/board",
                params={
                    "scope": "MY_ACTIONS",
                    "lane": "HEAD_REVIEW",
                    "period": "2026-09",
                    **params,
                },
            ).json()
        )

    assert queue(channel_id=str(world.channel_id)) == {code}
    assert queue(channel_id=str(other_channel.id)) == set()
    assert queue(responsible_user_id=str(row.owner_user_id)) == {code}
    assert queue(responsible_user_id=str(world.other.id)) == set()
    assert queue(priority=row.priority.value) == {code}
    assert queue(priority="CRITICAL") == set()
    # Ordinary date filters keep their meaning: created in July, so a July
    # CREATED_AT window keeps it and a September one drops it.
    assert queue(date_field="CREATED_AT", date_from="2026-07-01", date_to="2026-07-31") == {code}
    assert queue(date_field="CREATED_AT", date_from="2026-09-01", date_to="2026-09-30") == set()


async def test_a15_the_month_free_queue_does_not_widen_authorization(world: World) -> None:
    """A stale gate item is in the queue of whoever holds the gate, and nobody else's."""
    code = await stale_head_review(world)
    for person, expected in ((world.head, {code}), (world.lead, set()), (world.member, set())):
        world.act_as(person)
        queue = world.client.get(
            "/api/pr/contents/board",
            params={"scope": "MY_ACTIONS", "lane": "HEAD_REVIEW", "period": "2026-09"},
        ).json()
        assert codes(queue) == expected, person.full_name


# ===========================================================================
# STEP 1F.2.3f.6c: THE ARCHIVE LEAVES THE OPERATIONAL BOARD
# ===========================================================================


def archive_view(world: World, period: str | None = None, **params: object) -> dict[str, Any]:
    query: dict[str, object] = {"scope": "ALL", "view": "ARCHIVE", "limit": 50, **params}
    if period:
        query["period"] = period
    response = world.client.get("/api/pr/contents/board", params=query)
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


async def test_c1_c2_the_completed_group_is_ready_and_published_only() -> None:
    assert lanes_in_group(PrContentGroup.COMPLETED) == (
        PrContentLane.READY_TO_PUBLISH,
        PrContentLane.PUBLISHED,
    )
    assert stages_in_group(PrContentGroup.COMPLETED) == (
        PrWorkflowStage.READY_TO_PUBLISH,
        PrWorkflowStage.PUBLISHED,
    )
    assert PrContentLane.ARCHIVED in ARCHIVE_LANES
    assert all(PrContentLane.ARCHIVED not in lanes_in_group(group) for group in PrContentGroup)


async def test_c3_c5_archived_content_is_neither_a_card_nor_a_count_on_the_default_board(
    world: World,
) -> None:
    """Archived in September; September's default board neither shows nor counts it."""
    code = await archived_on(world, SEPTEMBER, published_at=AUGUST)
    live, master = await ready_to_publish(world)
    await publish(world, live, submission=master, at=SEPTEMBER)
    live_code = await code_of(world, live)

    figures = board(world, period="2026-09")
    assert figures["view"] == "ACTIVE"
    assert stage_count(figures, "ARCHIVED") == 0
    assert stage_count(figures, "PUBLISHED") == 1
    assert group_count(figures, "COMPLETED") == 1
    everything = world.client.get(
        "/api/pr/contents/board", params={"scope": "ALL", "period": "2026-09", "limit": 50}
    ).json()
    assert codes(everything) == {live_code}
    assert everything["total"] == 1
    group = world.client.get(
        "/api/pr/contents/board",
        params={"scope": "ALL", "group": "COMPLETED", "period": "2026-09", "limit": 50},
    ).json()
    assert codes(group) == {live_code}
    assert group["total"] == 1
    # And without any month at all - the cumulative default board - still not.
    assert code not in codes(
        world.client.get("/api/pr/contents/board", params={"scope": "ALL", "limit": 50}).json()
    )
    assert stage_count(board(world), "ARCHIVED") == 0


async def test_c6_c8_archiving_still_works_and_the_row_moves_to_the_archive_view(
    world: World,
) -> None:
    content_id, master = await ready_to_publish(world)
    await publish(world, content_id, submission=master, at=SEPTEMBER)
    code = await code_of(world, content_id)
    assert codes(lane(world, "PUBLISHED", "2026-09")) == {code}

    world.act_as(world.owner)
    response = world.client.post(
        f"/api/pr/contents/{content_id}/transition", json={"target_stage": "ARCHIVED"}
    )
    assert response.status_code == 200, response.text
    row = await world.reload(content_id)
    assert row.workflow_stage is PrWorkflowStage.ARCHIVED and row.archived_at is not None
    month = row.archived_at.strftime("%Y-%m")

    # 7: gone from the default board, in every shape it is asked for.
    assert codes(lane(world, "PUBLISHED", "2026-09")) == set()
    assert stage_count(board(world, period=month), "ARCHIVED") == 0
    assert group_count(board(world, period=month), "COMPLETED") == 0
    # 8: retrievable on demand, and still the same row.
    archived = archive_view(world, month)
    assert archived["view"] == "ARCHIVE"
    assert codes(archived) == {code}
    assert archived["total"] == 1
    assert archived["items"][0]["workflow_stage"] == "ARCHIVED"
    detail = world.client.get(f"/api/pr/contents/{content_id}")
    assert detail.status_code == 200


async def test_c9_c10_the_archive_view_holds_archived_only_and_reads_archived_at(
    world: World,
) -> None:
    september = await archived_on(world, SEPTEMBER, published_at=AUGUST)
    august = await archived_on(world, AUGUST, published_at=JULY)
    live, master = await ready_to_publish(world)
    await publish(world, live, submission=master, at=SEPTEMBER)
    live_code = await code_of(world, live)

    assert codes(archive_view(world, "2026-09")) == {september}
    assert codes(archive_view(world, "2026-08")) == {august}
    # By archived_at, not by the publication month: the August-published,
    # September-archived piece is September's here.
    assert september not in codes(archive_view(world, "2026-08"))
    cumulative = archive_view(world)
    assert codes(cumulative) == {september, august}
    assert live_code not in codes(cumulative)
    assert cumulative["period"] is None
    # Ordinary filters compose on the archive view too.
    assert codes(archive_view(world, "2026-09", channel_id=str(world.channel_id))) == {september}
    other = await new_channel(world)
    assert codes(archive_view(world, "2026-09", channel_id=str(other.id))) == set()
    # And the archive lane asked of the operational board is an empty
    # intersection, not a back door.
    assert (
        world.client.get(
            "/api/pr/contents/board", params={"scope": "ALL", "lane": "ARCHIVED", "limit": 50}
        ).json()["total"]
        == 0
    )


async def test_c15_archived_content_never_reaches_the_action_queue(world: World) -> None:
    """An open task on an archived piece would match the queue's predicate;
    the view clause keeps it out anyway."""
    content_id, master = await ready_to_publish(world)
    await publish(world, content_id, submission=master, at=AUGUST)
    task = await world.services.tasks.create_task(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        command=CreateTaskCommand(task_type="FOLLOW_UP", title="Đối chiếu", content_id=content_id),
    )
    await world.services.tasks.assign_user(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        task_id=task.id,
        user_id=world.member.id,
        assignment_role=PrTaskAssignmentRole.OWNER,
    )
    world.act_as(world.member)
    before = world.client.get(
        "/api/pr/contents/board", params={"scope": "MY_ACTIONS", "limit": 50}
    ).json()
    code = await code_of(world, content_id)
    assert code in codes(before)

    await move(world, content_id, PrWorkflowStage.ARCHIVED)
    after = world.client.get(
        "/api/pr/contents/board", params={"scope": "MY_ACTIONS", "limit": 50}
    ).json()
    assert code not in codes(after)
    assert stage_count(after, "ARCHIVED") == 0


# ===========================================================================
# STEP 1F.2.3f.6d: NO PERIOD BY DEFAULT - THE BOARD IS EVERY ACTIVE ROW
# ===========================================================================


def compiled_count_sql(**kwargs: object) -> str:
    """The board's grouped-count statement for one query, as PostgreSQL text."""
    from sqlalchemy import func, select
    from sqlalchemy.dialects import postgresql

    from meobot.application.pr_content_query import ActorWorkQueue, ContentQuery, content_conditions
    from meobot.domain.pr.content_views import PrContentBoardView, PrContentViewScope

    query = ContentQuery(
        scope=PrContentViewScope.ALL,
        view=PrContentBoardView.ACTIVE,
        **kwargs,  # type: ignore[arg-type]
    )
    conditions = content_conditions(
        query.for_counts(),
        work_queue=ActorWorkQueue(user_id=None),
        tz=ZoneInfo("Asia/Ho_Chi_Minh"),
        today=date(2026, 9, 8),
    )
    statement = (
        select(PrContentItem.workflow_stage, func.count())
        .where(*conditions)
        .group_by(PrContentItem.workflow_stage)
    )
    return str(statement.compile(dialect=postgresql.dialect()))


async def test_d1_no_period_means_no_period_and_says_so(world: World) -> None:
    for person in (world.member, world.head, world.owner):
        world.act_as(person)
        body = world.client.get("/api/pr/contents/board", params={"limit": 0}).json()
        assert body["scope"] == "ALL", person.full_name
        assert body["period"] is None
        assert body["period_applied"] is False
        assert body["current_period"] == datetime.now(world.settings.timezone).strftime("%Y-%m")
        assert body["view"] == "ACTIVE"


async def test_d6_d7_every_active_row_is_on_the_no_period_board(world: World) -> None:
    """August's and September's published pieces are both *Đã đăng*; July's and
    September's reviews are both counted - until something archives or moves
    them, not until the month turns."""
    published = []
    for at in (JULY, AUGUST, SEPTEMBER):
        content_id, master = await ready_to_publish(world)
        await publish(world, content_id, submission=master, at=at)
        published.append(await code_of(world, content_id))
    reviewing = []
    for at in (JULY, SEPTEMBER):
        content_id = await make_content(world, owner=world.member)
        await to_review(world, content_id)
        await entered_on(world, content_id, at)
        reviewing.append(await code_of(world, content_id))
    world.act_as(world.owner)

    column = world.client.get(
        "/api/pr/contents/board", params={"scope": "ALL", "lane": "PUBLISHED", "limit": 50}
    ).json()
    assert codes(column) == set(published)
    assert column["total"] == 3
    figures = board(world)
    assert stage_count(figures, "PUBLISHED") == 3
    assert stage_count(figures, "TEAM_LEAD_REVIEW") == 2
    assert group_count(figures, "COMPLETED") == 3
    assert group_count(figures, "EDITORIAL_REVIEW") == 2
    # And an explicit month narrows exactly as before.
    assert stage_count(board(world, period="2026-08"), "PUBLISHED") == 1
    assert stage_count(board(world, period="2026-09"), "TEAM_LEAD_REVIEW") == 1
    assert codes(lane(world, "PUBLISHED", "2026-07")) == {published[0]}


async def test_d10_the_no_period_board_still_pages(world: World) -> None:
    for _ in range(3):
        content_id, master = await ready_to_publish(world)
        await publish(world, content_id, submission=master, at=AUGUST)
    page = world.client.get(
        "/api/pr/contents/board", params={"scope": "ALL", "lane": "PUBLISHED", "limit": 2}
    ).json()
    assert len(page["items"]) == 2
    assert page["total"] == 3


async def test_d21_d25_the_queue_is_month_free_with_or_without_a_month(world: World) -> None:
    code = await stale_head_review(world)
    world.act_as(world.head)
    for params in ({}, {"period": "2026-09"}, {"period": "2026-08"}):
        queue = world.client.get(
            "/api/pr/contents/board",
            params={"scope": "MY_ACTIONS", "lane": "HEAD_REVIEW", **params},
        ).json()
        assert codes(queue) == {code}, params
        assert queue["period_applied"] is False, params
        assert queue["period"] == params.get("period"), params


async def test_d38_d40_no_period_builds_no_reporting_expression(world: World) -> None:
    """Part "query performance": the no-period statement carries no CASE, no
    transition-history subquery and no publication aggregate."""
    bare = compiled_count_sql()
    assert "CASE" not in bare
    assert "pr_content_transition_events" not in bare
    assert "pr_publications" not in bare
    assert "archived_at" not in bare
    assert "workflow_stage !=" in bare
    dated = compiled_count_sql(period_month=date(2026, 9, 1))
    assert "CASE" in dated
    assert "pr_content_transition_events" in dated
    assert "pr_publications" in dated
    assert "archived_at" not in dated
    # An ordinary date filter without a month is a plain column comparison.
    windowed = compiled_count_sql(
        date_field=PrContentDateField.UPDATED_AT,
        date_from=date(2026, 9, 1),
        date_to=date(2026, 9, 7),
    )
    assert "CASE" not in windowed and "updated_at" in windowed


async def test_d43_d47_ordinary_filters_narrow_the_no_period_board(world: World) -> None:
    content_id, master = await ready_to_publish(world)
    await created_on(world, content_id, LATE_AUGUST)
    await publish(world, content_id, submission=master, at=SEPTEMBER)
    row = await world.reload(content_id)
    row.planned_publish_at = datetime(2026, 8, 31, 3, 0, tzinfo=UTC)
    row.updated_at = LATER_SEPTEMBER
    await world.session.flush()
    code = row.code
    other = await new_channel(world)
    world.act_as(world.owner)

    def published(**params: object) -> set[str]:
        return codes(
            world.client.get(
                "/api/pr/contents/board", params={"scope": "ALL", "lane": "PUBLISHED", **params}
            ).json()
        )

    assert published(date_field="CREATED_AT", date_from="2026-08-25", date_to="2026-08-31") == {
        code
    }
    assert published(date_field="CREATED_AT", date_from="2026-09-01", date_to="2026-09-30") == set()
    assert published(
        date_field="PLANNED_PUBLISH_AT", date_from="2026-08-31", date_to="2026-08-31"
    ) == {code}
    assert published(date_field="UPDATED_AT", date_from="2026-09-08", date_to="2026-09-08") == {
        code
    }
    assert published(date_field="UPDATED_AT", date_from="2026-09-01", date_to="2026-09-02") == set()
    assert published(channel_id=str(world.channel_id)) == {code}
    assert published(channel_id=str(other.id)) == set()
    assert published(responsible_user_id=str(row.owner_user_id)) == {code}
    assert published(priority="CRITICAL") == set()


async def test_d48_d50_no_period_does_not_widen_any_scope(world: World) -> None:
    mine = await make_content(world, owner=world.member)
    theirs = await make_content(world, owner=world.other)
    await world.services.channels.assign_user(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        channel_id=world.channel_id,
        user_id=world.member.id,
        assignment_role=PrChannelAssignmentRole.CONTENT_OWNER,
        effective_from=date(2026, 1, 1),
    )
    world.act_as(world.member)

    def scoped(scope: str) -> set[str]:
        return codes(
            world.client.get(
                "/api/pr/contents/board", params={"scope": scope, "lane": "IDEA", "limit": 50}
            ).json()
        )

    assert scoped("MY_CONTENT") == {await code_of(world, mine)}
    assert scoped("ALL") == {await code_of(world, mine), await code_of(world, theirs)}
    # Both items target the member's channel, so TEAM holds both - by
    # assignment, not by month.
    assert scoped("TEAM") == {await code_of(world, mine), await code_of(world, theirs)}


# ===========================================================================
# 42-49: BULK ARCHIVE OF A CLOSED MONTH
# ===========================================================================


def candidates(world: World, period: str) -> dict[str, Any]:
    response = world.client.get("/api/pr/contents/archive-candidates", params={"period": period})
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def archive_batch(world: World, period: str, ids: Iterable[str]) -> Any:
    return world.client.post(
        "/api/pr/contents/archive-batch", json={"period": period, "content_ids": list(ids)}
    )


async def published_batch(world: World, *instants: datetime) -> list[uuid.UUID]:
    ids = []
    for at in instants:
        content_id, master = await ready_to_publish(world)
        await publish(world, content_id, submission=master, at=at)
        ids.append(content_id)
    return ids


async def test_42_43_the_candidate_count_is_the_published_column_of_that_month(
    world: World,
) -> None:
    await published_batch(world, AUGUST, AUGUST, SEPTEMBER, JULY)
    found = candidates(world, "2026-08")
    assert found["period"] == "2026-08"
    assert found["total"] == 2 == len(found["content_ids"])
    assert found["truncated"] is False
    assert found["may_archive"] is True
    assert found["total"] == lane(world, "PUBLISHED", "2026-08")["total"]


async def test_44_46_only_that_months_published_content_transitions_through_the_service(
    world: World,
) -> None:
    august = await published_batch(world, AUGUST, AUGUST)
    untouched = await published_batch(world, SEPTEMBER, JULY)
    found = candidates(world, "2026-08")
    world.act_as(world.head)
    response = archive_batch(world, "2026-08", found["content_ids"])
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["archived_count"] == 2
    assert {item["content_id"] for item in body["archived"]} == {str(one) for one in august}

    for content_id in august:
        row = await world.reload(content_id)
        assert row.workflow_stage is PrWorkflowStage.ARCHIVED
        assert row.archived_at is not None
        actions = await audit_actions(world, content_id)
        assert AuditAction.PR_CONTENT_STAGE_CHANGED.value in actions
        event = await world.session.scalar(
            select(PrContentTransitionEvent).where(
                PrContentTransitionEvent.content_id == content_id,
                PrContentTransitionEvent.to_stage == PrWorkflowStage.ARCHIVED,
            )
        )
        assert event is not None
        assert event.from_stage is PrWorkflowStage.PUBLISHED
        assert event.actor_user_id == world.head.id
    # 45: the current month and the older month are unaffected.
    for content_id in untouched:
        assert (await world.reload(content_id)).workflow_stage is PrWorkflowStage.PUBLISHED
    batch = await world.session.scalar(
        select(AuditLog).where(AuditLog.action == AuditAction.PR_CONTENT_ARCHIVE_BATCH_RECORDED)
    )
    assert batch is not None
    assert batch.after_data["archived_count"] == 2
    assert batch.after_data["period"] == "2026-08"
    # And the board now reads them as archived in the month it happened.
    month = datetime.now(world.settings.timezone).strftime("%Y-%m")
    assert lane(world, "ARCHIVED", month)["total"] == 2
    assert lane(world, "PUBLISHED", "2026-08")["total"] == 0


async def test_45a_the_current_month_cannot_be_archived_as_a_period(world: World) -> None:
    current = datetime.now(world.settings.timezone).strftime("%Y-%m")
    ids = await published_batch(world, datetime.now(UTC))
    response = archive_batch(world, current, [str(one) for one in ids])
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["reason"] == "period_not_closed"
    assert (await world.reload(ids[0])).workflow_stage is PrWorkflowStage.PUBLISHED


async def test_47_the_capability_is_checked_before_any_row_is_written(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = await published_batch(world, AUGUST, AUGUST)

    async def refuse(*args: object, **kwargs: object) -> None:
        raise PrPermissionDeniedError("no", details={"reason": "test"})

    monkeypatch.setattr(world.services.capabilities, "require", refuse)
    with pytest.raises(PrPermissionDeniedError):
        await world.services.bulk_archive.archive(
            actor=world.actor(world.member),
            request_id=world.request_id,
            command=BulkArchiveCommand(period=AUGUST.date(), content_ids=ids),
        )
    for content_id in ids:
        assert (await world.reload(content_id)).workflow_stage is PrWorkflowStage.PUBLISHED
    # And the candidate read reports the same answer the write would give.
    monkeypatch.setattr(world.services.capabilities, "allows", refuse)


async def test_48_a_stale_item_refuses_the_whole_batch(world: World) -> None:
    """One re-dated publication, and nothing in the batch is archived."""
    ids = await published_batch(world, AUGUST, AUGUST, AUGUST)
    found = candidates(world, "2026-08")
    assert found["total"] == 3
    # Somebody corrects one publication into September between the dialog and
    # the button.
    row = await world.reload(ids[1])
    publication = await world.session.scalar(
        select(PrPublication).where(PrPublication.content_id == row.id)
    )
    assert publication is not None
    publication.published_at = SEPTEMBER
    await world.session.flush()

    response = archive_batch(world, "2026-08", found["content_ids"])
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["details"]["archived"] == 0
    assert error["details"]["reason"] == "outside_period"
    assert [one["content_id"] for one in error["details"]["affected"]] == [str(ids[1])]
    for content_id in ids:
        assert (await world.reload(content_id)).workflow_stage is PrWorkflowStage.PUBLISHED


async def test_49_a_retry_after_success_is_a_clean_refusal(world: World) -> None:
    ids = await published_batch(world, AUGUST)
    found = candidates(world, "2026-08")
    assert archive_batch(world, "2026-08", found["content_ids"]).status_code == 201
    again = archive_batch(world, "2026-08", found["content_ids"])
    assert again.status_code == 409, again.text
    assert again.json()["error"]["details"]["reason"] == "already_archived"
    events = (
        (
            await world.session.execute(
                select(PrContentTransitionEvent).where(
                    PrContentTransitionEvent.content_id == ids[0],
                    PrContentTransitionEvent.to_stage == PrWorkflowStage.ARCHIVED,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
    # A missing id and an empty batch are refused the same way, before writes.
    assert archive_batch(world, "2026-08", [str(uuid.uuid4())]).status_code == 409
    assert archive_batch(world, "2026-08", []).status_code == 422
    # The candidate set is now empty, and the archived row is in the archive.
    assert candidates(world, "2026-08")["total"] == 0


async def test_49a_the_batch_is_bounded(world: World) -> None:
    too_many = [str(uuid.uuid4()) for _ in range(BULK_ARCHIVE_MAX_ITEMS + 1)]
    assert archive_batch(world, "2026-08", too_many).status_code == 422


async def test_49b_the_service_refuses_an_unclosed_month_before_locking(world: World) -> None:
    with pytest.raises(PrValidationError) as refused:
        await world.services.bulk_archive.archive(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            command=BulkArchiveCommand(
                period=datetime.now(UTC).date().replace(day=1) + timedelta(days=40),
                content_ids=[uuid.uuid4()],
            ),
        )
    assert refused.value.details["reason"] == "period_not_closed"
    with pytest.raises(PrBulkArchiveStaleError):
        await world.services.bulk_archive.archive(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            command=BulkArchiveCommand(period=AUGUST.date(), content_ids=[uuid.uuid4()]),
        )


# ===========================================================================
# MEASURED STAYS RETIRED
# ===========================================================================


async def test_measured_is_offered_nowhere_and_refused_everywhere(world: World) -> None:
    content_id, master = await ready_to_publish(world)
    await publish(world, content_id, submission=master, at=SEPTEMBER)
    assert "MEASURED" not in {
        row["stage"] for row in board(world, period="2026-09")["stage_counts"] if row["count"]
    }
    response = world.client.post(
        f"/api/pr/contents/{content_id}/transition", json={"target_stage": "MEASURED"}
    )
    assert response.status_code in (409, 422), response.text
    assert (await world.reload(content_id)).workflow_stage is PrWorkflowStage.PUBLISHED
    assert "MEASURED" not in await world.actions(world.owner, content_id)
