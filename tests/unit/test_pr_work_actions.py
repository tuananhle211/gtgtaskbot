"""The Work action contract: one flag per lifecycle write, resolved on the server.

The bug this pins: a ``REJECTED`` job's detail offered *Hủy*, and the cancel
route refused it with ``Cannot move work from 'REJECTED' to 'CANCELLED'``.
The state machine was right and the screen was wrong, because the screen
inferred every button from ``status`` and three coarse per-actor flags - a
second copy of the transition table, and one that had drifted.

Now :func:`~meobot.application.pr_work_service.resolve_work_actions` answers
``can_accept`` … ``can_cancel`` from :data:`~meobot.domain.pr.work.WORK_TRANSITIONS`
and the same guards the writes apply, the detail carries the seven flags, and
the screen draws from them alone. This suite is the matrix that keeps the
resolver honest against the writes:

* **the table** - every status against every target, and which of them may
  reach ``CANCELLED``: ``PROPOSED``, ``ACCEPTED``, ``IN_PROGRESS``,
  ``COMPLETED``. ``REJECTED`` and ``CANCELLED`` reach nothing;
* **the matrix** - every real status, for the item's manager, a contributor,
  an independent validator and the proposer, with the expected seven flags;
* **agreement** - for each status and role, every flag the resolver sets
  true names a write that succeeds, and every flag it sets false names a
  write that is refused with a 4xx and changes nothing. Over HTTP for the
  two terminal statuses, so the guard is proved where a forged request lands;
* **the terminal rows** - ``REJECTED`` and ``CANCELLED`` get no action from
  anybody, and the cancelled row keeps its safe-delete flag for an
  administrator, which is a different rule and stays one.

Nothing here contacts a network.
"""

from __future__ import annotations

# ruff: noqa: F811 - `world` is a fixture imported from the production suite
import uuid
from dataclasses import asdict

import pytest
from sqlalchemy import func, select

from meobot.application.pr_work_service import CreateWorkCommand, WorkActions
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.pr_work import PrWorkItem, PrWorkType
from meobot.db.models.user import User
from meobot.domain.pr.errors import PrPermissionDeniedError, PrValidationError
from meobot.domain.pr.work import (
    TERMINAL_WORK_STATUSES,
    WORK_TRANSITIONS,
    PrWorkStatus,
    can_transition_work,
)
from tests.unit.test_pr_content_work_projection import work_type
from tests.unit.test_pr_production_lifecycle import World, world  # noqa: F401
from tests.unit.test_pr_work_core import assigned, take_to_completed
from tests.unit.test_pr_work_maintenance import count

pytestmark = pytest.mark.asyncio

STATUSES = [
    PrWorkStatus.PROPOSED,
    PrWorkStatus.ACCEPTED,
    PrWorkStatus.IN_PROGRESS,
    PrWorkStatus.COMPLETED,
    PrWorkStatus.APPROVED,
    PrWorkStatus.REJECTED,
    PrWorkStatus.CANCELLED,
]
NONE = WorkActions()


# ===========================================================================
# Helpers
# ===========================================================================


async def a_type(world: World, code: str = "SHOOT") -> PrWorkType:
    existing = (
        await world.session.execute(select(PrWorkType).where(PrWorkType.code == code))
    ).scalar_one_or_none()
    return existing or await work_type(world, code=code, name=f"Loại {code}")


async def item_at(world: World, status: PrWorkStatus, *, title: str = "Quay TVC") -> PrWorkItem:
    """A **manual** job carried to ``status`` through the real writes.

    Proposals are the member's; everything else is the lead's assignment,
    started and finished by the member and validated by the head. Every edge
    taken here is one the table admits, so the row is a real one.
    """
    type_row = await a_type(world)
    if status in {PrWorkStatus.PROPOSED, PrWorkStatus.REJECTED}:
        item = await world.services.work.propose_work(
            actor=world.actor(world.member),
            request_id=world.request_id,
            command=CreateWorkCommand(
                title=title, work_type_id=type_row.id, contributor_user_ids=(world.member.id,)
            ),
        )
        if status is PrWorkStatus.REJECTED:
            item = await world.services.work.reject(
                actor=world.actor(world.lead),
                request_id=world.request_id,
                work_item_id=item.id,
                reason="Không phù hợp",
            )
        return item
    item = await assigned(world, title=title, type_row=type_row)
    if status is PrWorkStatus.ACCEPTED:
        return item
    if status is PrWorkStatus.CANCELLED:
        return await world.services.work.cancel(
            actor=world.actor(world.lead), request_id=world.request_id, work_item_id=item.id
        )
    if status is PrWorkStatus.IN_PROGRESS:
        return await world.services.work.start(
            actor=world.actor(world.member), request_id=world.request_id, work_item_id=item.id
        )
    item = await take_to_completed(world, item, by=world.member)
    if status is PrWorkStatus.COMPLETED:
        return item
    assert status is PrWorkStatus.APPROVED
    await world.services.work.approve(
        actor=world.actor(world.head), request_id=world.request_id, work_item_id=item.id
    )
    approved = await world.session.get(PrWorkItem, item.id)
    assert approved is not None
    return approved


async def actions_of(world: World, user: User, item_id: uuid.UUID) -> WorkActions:
    """The contract as this user reads it. A row they may not read offers
    nothing - the lead who rejected the member's proposal is no longer
    deciding about it, is not its owner, and gets a 403 on the detail."""
    try:
        detail = await world.services.work.detail(actor=world.actor(user), work_item_id=item_id)
    except PrPermissionDeniedError:
        return NONE
    return detail.actions


def expected(status: PrWorkStatus, role: str) -> WorkActions:
    """The matrix, written out by hand from the writes' own guards.

    ``manager`` is the lead who assigned the row (its manager, and for a
    proposal a manager who is not the proposer and not its owner);
    ``contributor`` is the member, who proposed the proposals; ``validator``
    is the head, who holds every capability including ``PR_WORK_VIEW_ALL`` -
    so every row's manager - and is the proposer of nothing.
    """
    if role == "manager":
        # A proposal is the member's own row - the lead neither created nor
        # assigned it - so the lead may decide it but not withdraw it: cancel
        # is the item manager's act, and ``_require_item_manager`` refuses.
        return {
            PrWorkStatus.PROPOSED: WorkActions(can_accept=True, can_reject=True),
            PrWorkStatus.ACCEPTED: WorkActions(can_start=True, can_complete=True, can_cancel=True),
            PrWorkStatus.IN_PROGRESS: WorkActions(can_complete=True, can_cancel=True),
            PrWorkStatus.COMPLETED: WorkActions(can_approve=True, can_reopen=True, can_cancel=True),
            PrWorkStatus.APPROVED: NONE,
            PrWorkStatus.REJECTED: NONE,
            PrWorkStatus.CANCELLED: NONE,
        }[status]
    if role == "contributor":
        return {
            PrWorkStatus.PROPOSED: NONE,
            PrWorkStatus.ACCEPTED: WorkActions(can_start=True, can_complete=True),
            PrWorkStatus.IN_PROGRESS: WorkActions(can_complete=True),
            PrWorkStatus.COMPLETED: NONE,
            PrWorkStatus.APPROVED: NONE,
            PrWorkStatus.REJECTED: NONE,
            PrWorkStatus.CANCELLED: NONE,
        }[status]
    if role == "validator":
        return {
            PrWorkStatus.PROPOSED: WorkActions(can_accept=True, can_reject=True, can_cancel=True),
            PrWorkStatus.ACCEPTED: WorkActions(can_start=True, can_complete=True, can_cancel=True),
            PrWorkStatus.IN_PROGRESS: WorkActions(can_complete=True, can_cancel=True),
            PrWorkStatus.COMPLETED: WorkActions(can_approve=True, can_reopen=True, can_cancel=True),
            PrWorkStatus.APPROVED: NONE,
            PrWorkStatus.REJECTED: NONE,
            PrWorkStatus.CANCELLED: NONE,
        }[status]
    raise AssertionError(role)


ROLE_USER = {"manager": "lead", "contributor": "member", "validator": "head"}


# ===========================================================================
# The table
# ===========================================================================


async def test_the_transition_table_is_the_canonical_lifecycle() -> None:
    """Documented here as the matrix the rest of the suite is measured against."""
    assert dict(WORK_TRANSITIONS) == {
        PrWorkStatus.PROPOSED: {
            PrWorkStatus.ACCEPTED,
            PrWorkStatus.REJECTED,
            PrWorkStatus.CANCELLED,
        },
        PrWorkStatus.ACCEPTED: {
            PrWorkStatus.IN_PROGRESS,
            PrWorkStatus.COMPLETED,
            PrWorkStatus.CANCELLED,
        },
        PrWorkStatus.IN_PROGRESS: {PrWorkStatus.COMPLETED, PrWorkStatus.CANCELLED},
        PrWorkStatus.COMPLETED: {
            PrWorkStatus.APPROVED,
            PrWorkStatus.IN_PROGRESS,
            PrWorkStatus.CANCELLED,
        },
        PrWorkStatus.APPROVED: {PrWorkStatus.COMPLETED},
        PrWorkStatus.REJECTED: set(),
        PrWorkStatus.CANCELLED: set(),
    }
    assert {s for s in STATUSES if can_transition_work(s, PrWorkStatus.CANCELLED)} == {
        PrWorkStatus.PROPOSED,
        PrWorkStatus.ACCEPTED,
        PrWorkStatus.IN_PROGRESS,
        PrWorkStatus.COMPLETED,
    }
    assert PrWorkStatus.REJECTED in TERMINAL_WORK_STATUSES
    assert PrWorkStatus.CANCELLED in TERMINAL_WORK_STATUSES
    # ``APPROVED → COMPLETED`` is the projector's reversal, never a person's.
    assert not can_transition_work(PrWorkStatus.APPROVED, PrWorkStatus.COMPLETED)
    assert can_transition_work(PrWorkStatus.APPROVED, PrWorkStatus.COMPLETED, source_derived=True)


# ===========================================================================
# The matrix
# ===========================================================================


@pytest.mark.parametrize("status", STATUSES)
@pytest.mark.parametrize("role", ["manager", "contributor", "validator"])
async def test_the_resolver_matches_the_matrix(
    world: World, status: PrWorkStatus, role: str
) -> None:
    item = await item_at(world, status)
    assert item.status is status
    user = getattr(world, ROLE_USER[role])
    assert (await actions_of(world, user, item.id)) == expected(status, role), (status, role)


@pytest.mark.parametrize("status", STATUSES)
async def test_the_detail_exposes_the_same_flags_over_http(
    world: World, status: PrWorkStatus
) -> None:
    item = await item_at(world, status)
    for role, name in ROLE_USER.items():
        world.act_as(getattr(world, name))
        response = world.client.get(f"/api/pr/work/{item.id}")
        if response.status_code == 403:
            assert expected(status, role) == NONE, (status, role)
            continue
        assert response.status_code == 200, (status, role, response.text)
        body = response.json()
        assert {key: body[key] for key in asdict(NONE)} == asdict(expected(status, role)), (
            status,
            role,
        )


async def test_the_proposer_may_neither_accept_nor_reject_their_own_proposal(
    world: World,
) -> None:
    """A lead proposing for themselves holds ``PR_WORK_MANAGE`` and still gets no
    accept or reject: the write refuses ``self_acceptance``, so the flag is false."""
    type_row = await a_type(world)
    item = await world.services.work.propose_work(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        command=CreateWorkCommand(
            title="Tự đề xuất", work_type_id=type_row.id, contributor_user_ids=(world.lead.id,)
        ),
    )
    mine = await actions_of(world, world.lead, item.id)
    assert mine.can_accept is False and mine.can_reject is False
    # It is their own book, so they may still withdraw it.
    assert mine.can_cancel is True
    with pytest.raises(PrPermissionDeniedError) as caught:
        await world.services.work.accept(
            actor=world.actor(world.lead), request_id=world.request_id, work_item_id=item.id
        )
    assert caught.value.details["reason"] == "self_acceptance"
    # Another manager may.
    theirs = await actions_of(world, world.head, item.id)
    assert theirs.can_accept is True and theirs.can_reject is True


# ===========================================================================
# Agreement: a true flag is a write that succeeds; a false one is refused
# ===========================================================================


WRITE = {
    "can_accept": "accept",
    "can_reject": "reject",
    "can_start": "start",
    "can_complete": "complete",
    "can_approve": "approve",
    "can_reopen": "reopen",
    "can_cancel": "cancel",
}


@pytest.mark.parametrize("status", STATUSES)
@pytest.mark.parametrize("role", ["manager", "contributor", "validator"])
async def test_every_false_flag_is_a_refused_write_that_changes_nothing(
    world: World, status: PrWorkStatus, role: str
) -> None:
    user = getattr(world, ROLE_USER[role])
    flags = asdict(expected(status, role))
    for flag, method in WRITE.items():
        if flags[flag]:
            continue
        item = await item_at(world, status, title=f"{status.value} {flag}")
        before = (item.status, item.completed_at, item.approved_at, item.cancelled_at)
        audits = await count(world, select(func.count()).select_from(AuditLog))
        # Every guard runs before the first write, so a refusal leaves the
        # session clean - no rollback, which would also undo the fixture.
        with pytest.raises((PrValidationError, PrPermissionDeniedError)):
            await getattr(world.services.work, method)(
                actor=world.actor(user), request_id=world.request_id, work_item_id=item.id
            )
        await world.session.flush()
        fresh = await world.session.get(PrWorkItem, item.id)
        assert fresh is not None
        assert (fresh.status, fresh.completed_at, fresh.approved_at, fresh.cancelled_at) == (
            before
        ), (status, role, flag)
        assert await count(world, select(func.count()).select_from(AuditLog)) == audits


@pytest.mark.parametrize("status", STATUSES)
@pytest.mark.parametrize("role", ["manager", "contributor", "validator"])
async def test_every_true_flag_is_a_write_that_succeeds(
    world: World, status: PrWorkStatus, role: str
) -> None:
    user = getattr(world, ROLE_USER[role])
    flags = asdict(expected(status, role))
    for flag, method in WRITE.items():
        if not flags[flag]:
            continue
        item = await item_at(world, status, title=f"{status.value} {flag}")
        await getattr(world.services.work, method)(
            actor=world.actor(user), request_id=world.request_id, work_item_id=item.id
        )
        moved = await world.session.get(PrWorkItem, item.id)
        assert moved is not None and moved.status is not status, (status, role, flag)


# ===========================================================================
# The terminal rows, over HTTP - where a forged request lands
# ===========================================================================


@pytest.mark.parametrize("status", [PrWorkStatus.REJECTED, PrWorkStatus.CANCELLED])
@pytest.mark.parametrize("name", ["owner", "head", "lead", "member"])
async def test_a_direct_cancel_of_a_terminal_row_is_a_deterministic_4xx(
    world: World, status: PrWorkStatus, name: str
) -> None:
    item = await item_at(world, status)
    world.act_as(getattr(world, name))
    detail = world.client.get(f"/api/pr/work/{item.id}")
    # A rejected proposal is the member's own row; the lead who rejected it
    # is no longer deciding about it and may not read it - and still may not
    # cancel it. Everybody who can read it gets ``can_cancel: false``.
    assert detail.status_code in {200, 403}, detail.text
    if detail.status_code == 200:
        assert detail.json()["can_cancel"] is False
        assert detail.json()["item"]["status_label"] == (
            "Không được chấp nhận" if status is PrWorkStatus.REJECTED else "Đã hủy"
        )
    audits = await count(world, select(func.count()).select_from(AuditLog))
    response = world.client.post(f"/api/pr/work/{item.id}/cancel", json={"note": None})
    assert 400 <= response.status_code < 500, response.text
    assert response.status_code != 500
    if response.status_code != 403:
        details = response.json()["error"]["details"]
        assert details["reason"] == "illegal_transition"
        assert details["current"] == status.value
        assert details["target"] == "CANCELLED"
    fresh = await world.session.get(PrWorkItem, item.id)
    assert fresh is not None and fresh.status is status
    assert await count(world, select(func.count()).select_from(AuditLog)) == audits


@pytest.mark.parametrize("status", [PrWorkStatus.REJECTED, PrWorkStatus.CANCELLED])
async def test_a_terminal_row_offers_nobody_any_lifecycle_action(
    world: World, status: PrWorkStatus
) -> None:
    item = await item_at(world, status)
    for name in ("owner", "head", "lead", "member", "other"):
        user = getattr(world, name)
        try:
            actions = await actions_of(world, user, item.id)
        except PrPermissionDeniedError:
            continue  # may not even read it; then there is nothing to offer
        assert actions == NONE, (status, name)


async def test_both_terminal_rows_keep_the_safe_delete_flag_and_no_cancel(
    world: World,
) -> None:
    """Reject, cancel and delete are three different things. Delete is the
    maintenance rule (terminal safe-delete), decided separately from the
    lifecycle: a rejected proposal and a cancelled job both get it when clean,
    and neither gets *Hủy* - there is no edge to cancel from either."""
    for status in (PrWorkStatus.CANCELLED, PrWorkStatus.REJECTED):
        item = await item_at(world, status)
        world.act_as(world.owner)
        body = world.client.get(f"/api/pr/work/{item.id}").json()
        assert body["can_cancel"] is False, status
        assert body["can_admin_delete"] is True, status
        assert body["admin_delete"]["rule"] == "terminal"
        assert body["admin_delete"]["previous_status"] == status.value
        assert body["can_delete_legacy"] is False
    # An in-flight row gets neither the flag nor the reasoning.
    active = await item_at(world, PrWorkStatus.ACCEPTED)
    body = world.client.get(f"/api/pr/work/{active.id}").json()
    assert body["can_admin_delete"] is False and body["admin_delete"] is None
