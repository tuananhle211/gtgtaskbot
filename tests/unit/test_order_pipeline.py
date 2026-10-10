"""The order pipeline's rules, with no database: plans, transitions, policy."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from meobot.domain.orders.errors import OrderActionNotAllowedError, OrderStateError
from meobot.domain.orders.labels import PROCESS_SEPARATOR as SEP
from meobot.domain.orders.models import (
    ACTIVE_NODE_STATUSES,
    NODE_PLAN,
    OrderNodeStatus,
    OrderNodeType,
    OrderStage,
    OrderVideoType,
)
from meobot.domain.orders.pipeline import (
    NodeView,
    OrderActionKind,
    OrderActorContext,
    OrderView,
    activation_status,
    assert_allowed,
    available_actions,
    first_node,
    format_order_code,
    initial_node_statuses,
    is_urgent,
    next_node,
    stage_for_node,
)
from meobot.domain.units.models import (
    UnitCode,
    UnitMemberRole,
    UnitMembership,
    UnitMembershipEntry,
    UnitSettings,
)

OWNER = uuid.uuid4()
LEAD_BT = uuid.uuid4()
EDITOR = uuid.uuid4()
HEAD = uuid.uuid4()
UNIT = uuid.uuid4()


def test_01_each_video_type_visits_its_nodes_and_skips_the_rest() -> None:
    assert initial_node_statuses(OrderVideoType.D) == {
        OrderNodeType.BIEN_TAP: OrderNodeStatus.BO_QUA,
        OrderNodeType.THIET_KE: OrderNodeStatus.BO_QUA,
        OrderNodeType.DUNG: OrderNodeStatus.CHUA_TOI,
    }
    assert (
        initial_node_statuses(OrderVideoType.TD)[OrderNodeType.THIET_KE] is OrderNodeStatus.CHUA_TOI
    )
    assert (
        initial_node_statuses(OrderVideoType.TD)[OrderNodeType.BIEN_TAP] is OrderNodeStatus.BO_QUA
    )
    assert all(
        status is OrderNodeStatus.CHUA_TOI
        for status in initial_node_statuses(OrderVideoType.BTD).values()
    )
    for video_type, plan in NODE_PLAN.items():
        # No link node any more: no new order plans one.
        assert OrderNodeType.GAN_LINK not in plan, video_type
        assert OrderNodeType.GAN_LINK not in initial_node_statuses(video_type), video_type
        assert first_node(video_type) is plan[0]


def test_02_next_node_walks_the_plan_and_ends_after_the_last_production_node() -> None:
    from meobot.domain.orders.models import last_production_node
    from meobot.domain.orders.pipeline import hands_in_product

    assert next_node(OrderVideoType.BTD, OrderNodeType.BIEN_TAP) is OrderNodeType.THIET_KE
    assert next_node(OrderVideoType.BTD, OrderNodeType.DUNG) is None
    assert next_node(OrderVideoType.D, OrderNodeType.DUNG) is None
    # A legacy link node is past every production node.
    assert next_node(OrderVideoType.D, OrderNodeType.GAN_LINK) is None
    with pytest.raises(ValueError):
        next_node(OrderVideoType.D, OrderNodeType.BIEN_TAP)
    assert stage_for_node(OrderNodeType.THIET_KE) is OrderStage.THIET_KE
    # The last production node hands in the product.
    assert last_production_node(OrderVideoType.BTD) is OrderNodeType.DUNG
    assert last_production_node(OrderVideoType.BT) is OrderNodeType.THIET_KE
    assert last_production_node(OrderVideoType.B) is OrderNodeType.BIEN_TAP
    assert hands_in_product(OrderVideoType.BTD, OrderNodeType.DUNG)
    assert not hands_in_product(OrderVideoType.BTD, OrderNodeType.THIET_KE)
    assert hands_in_product(OrderVideoType.BT, OrderNodeType.THIET_KE)
    assert hands_in_product(OrderVideoType.D, OrderNodeType.GAN_LINK)


def test_03_a_chosen_person_starts_at_once_and_nobody_waits_for_the_leader() -> None:
    assert activation_status(True) is OrderNodeStatus.DANG_LAM
    assert activation_status(False) is OrderNodeStatus.CHUA_GIAO
    assert OrderNodeStatus.CHUA_GIAO in ACTIVE_NODE_STATUSES
    assert OrderNodeStatus.HOAN_THANH not in ACTIVE_NODE_STATUSES
    assert OrderNodeStatus.BO_QUA not in ACTIVE_NODE_STATUSES


def test_04_urgency_is_seven_days_and_never_for_a_finished_order() -> None:
    submitted = datetime(2026, 10, 1, 3, 0, tzinfo=UTC)
    late = submitted + timedelta(days=7, hours=1)
    assert is_urgent(submitted_at=submitted, now=late, stage=OrderStage.DUNG, urgent_days=7)
    assert not is_urgent(
        submitted_at=submitted,
        now=submitted + timedelta(days=6),
        stage=OrderStage.DUNG,
        urgent_days=7,
    )
    assert not is_urgent(
        submitted_at=submitted, now=late, stage=OrderStage.COMPLETED, urgent_days=7
    )
    assert not is_urgent(
        submitted_at=submitted, now=late, stage=OrderStage.CANCELLED, urgent_days=7
    )
    assert is_urgent(
        submitted_at=submitted, now=late, stage=OrderStage.ORDER_PENDING, urgent_days=3
    )


def test_05_the_code_reads_who_what_when_and_which() -> None:
    day = datetime(2026, 10, 3, 9, 0, tzinfo=UTC)
    assert format_order_code("TUAN", OrderVideoType.D, day, 1) == "TUAN-D-261003-01"
    assert format_order_code("LAN", OrderVideoType.BTD, day, 12) == "LAN-BTD-261003-12"


# --- policy -------------------------------------------------------------------


def membership(
    role: UnitMemberRole, user_id: uuid.UUID, *, is_lead: bool = False
) -> UnitMembership:
    return UnitMembership(
        user_id=user_id,
        entries=(
            UnitMembershipEntry(
                unit_id=UNIT, unit_code=UnitCode.ADS, role=role, is_lead=is_lead, member_code="X"
            ),
        ),
    )


def ctx(role: UnitMemberRole, user_id: uuid.UUID, *, is_lead: bool = False) -> OrderActorContext:
    return OrderActorContext.from_membership(
        membership(role, user_id, is_lead=is_lead), UnitSettings()
    )


def order(stage: OrderStage, *, video_type: OrderVideoType = OrderVideoType.BTD) -> OrderView:
    return OrderView(
        id=uuid.uuid4(), video_type=video_type, stage=stage, owner_user_id=OWNER, is_priority=False
    )


def node(
    node_type: OrderNodeType,
    status: OrderNodeStatus,
    *,
    assignee: uuid.UUID | None = None,
    accepted: bool = False,
) -> NodeView:
    return NodeView(
        id=uuid.uuid4(),
        node_type=node_type,
        status=status,
        assignee_user_id=assignee,
        preassigned_user_id=None,
        accepted_at=datetime.now(UTC) if accepted else None,
    )


def kinds(actions) -> set[OrderActionKind]:  # type: ignore[no-untyped-def]
    return {action.kind for action in actions}


def test_06_the_head_decides_the_order_gate_and_the_owner_resubmits() -> None:
    pending = order(OrderStage.ORDER_PENDING)
    nodes = (node(OrderNodeType.BIEN_TAP, OrderNodeStatus.CHUA_TOI),)
    head = ctx(UnitMemberRole.HEAD, HEAD)
    assert kinds(available_actions(pending, nodes, head)) == {
        OrderActionKind.APPROVE_ORDER,
        OrderActionKind.RETURN_ORDER,
        OrderActionKind.SET_PRIORITY,
        OrderActionKind.CANCEL,
    }
    owner = ctx(UnitMemberRole.ORDERER, OWNER)
    assert kinds(available_actions(pending, nodes, owner)) == {OrderActionKind.CANCEL}
    returned = order(OrderStage.ORDER_RETURNED)
    assert kinds(available_actions(returned, nodes, owner)) == {
        OrderActionKind.RESUBMIT,
        OrderActionKind.CANCEL,
    }
    assert kinds(available_actions(returned, nodes, head)) == {
        OrderActionKind.SET_PRIORITY,
        OrderActionKind.CANCEL,
    }
    # The OWNER of MeoChat acts as head without a tag.
    owner_of_everything = OrderActorContext.from_membership(
        UnitMembership(user_id=uuid.uuid4(), entries=(), is_owner=True), UnitSettings()
    )
    assert OrderActionKind.APPROVE_ORDER in kinds(
        available_actions(pending, nodes, owner_of_everything)
    )


def test_07_a_leader_assigns_and_approves_and_the_assignee_accepts_and_submits() -> None:
    working = order(OrderStage.BIEN_TAP)
    waiting = (node(OrderNodeType.BIEN_TAP, OrderNodeStatus.CHUA_GIAO),)
    lead = ctx(UnitMemberRole.BIEN_TAP, LEAD_BT, is_lead=True)
    writer = ctx(UnitMemberRole.BIEN_TAP, EDITOR)
    # The Leader hands it out, or changes its tokens / deadline (0053).
    assert kinds(available_actions(working, waiting, lead)) == {
        OrderActionKind.ASSIGN,
        OrderActionKind.SET_NODE_PLAN,
    }
    assert kinds(available_actions(working, waiting, writer)) == set()

    # Assigned is not accepted: the writer must take it before handing in.
    assigned = (node(OrderNodeType.BIEN_TAP, OrderNodeStatus.DANG_LAM, assignee=EDITOR),)
    assert kinds(available_actions(working, assigned, writer)) == {OrderActionKind.ACCEPT}
    with pytest.raises(OrderActionNotAllowedError):
        assert_allowed(OrderActionKind.SUBMIT_WORK, working, assigned, writer)
    accepted = (
        node(OrderNodeType.BIEN_TAP, OrderNodeStatus.DANG_LAM, assignee=EDITOR, accepted=True),
    )
    assert kinds(available_actions(working, accepted, writer)) == {OrderActionKind.SUBMIT_WORK}
    # Sent back for a fix: the same person works on, no new acceptance...
    fixing = (
        node(OrderNodeType.BIEN_TAP, OrderNodeStatus.DANG_SUA, assignee=EDITOR, accepted=True),
    )
    assert kinds(available_actions(working, fixing, writer)) == {OrderActionKind.SUBMIT_WORK}
    # ...unless the fix was handed to somebody new, who accepts first.
    handed_over = (node(OrderNodeType.BIEN_TAP, OrderNodeStatus.DANG_SUA, assignee=EDITOR),)
    assert kinds(available_actions(working, handed_over, writer)) == {OrderActionKind.ACCEPT}
    # The Leader may still hand it to somebody else while it is being worked.
    assert kinds(available_actions(working, accepted, lead)) == {
        OrderActionKind.ASSIGN,
        OrderActionKind.SET_NODE_PLAN,
    }

    handed_in = (node(OrderNodeType.BIEN_TAP, OrderNodeStatus.CHO_DUYET, assignee=EDITOR),)
    assert kinds(available_actions(working, handed_in, lead)) == {
        OrderActionKind.APPROVE_NODE,
        OrderActionKind.RETURN_NODE,
        OrderActionKind.SET_NODE_PLAN,
    }
    assert kinds(available_actions(working, handed_in, writer)) == set()
    # A design lead has nothing to do with a script node.
    design_lead = ctx(UnitMemberRole.THIET_KE, uuid.uuid4(), is_lead=True)
    assert kinds(available_actions(working, handed_in, design_lead)) == set()


def test_08_no_link_step_is_offered_and_a_legacy_link_node_is_the_last_nodes() -> None:
    assert "ATTACH_LINK" not in {kind.value for kind in OrderActionKind}
    # A legacy order still at the old link step: its assignee takes it and
    # hands the link in like the last production node would.
    linking = order(OrderStage.GAN_LINK)
    editor = ctx(UnitMemberRole.DUNG, EDITOR)
    taken = (node(OrderNodeType.GAN_LINK, OrderNodeStatus.DANG_LAM, assignee=EDITOR),)
    assert kinds(available_actions(linking, taken, editor)) == {OrderActionKind.ACCEPT}
    accepted = (
        node(OrderNodeType.GAN_LINK, OrderNodeStatus.DANG_LAM, assignee=EDITOR, accepted=True),
    )
    assert kinds(available_actions(linking, accepted, editor)) == {OrderActionKind.SUBMIT_WORK}
    # It is handed out by the function of the process's last production node.
    unassigned = (node(OrderNodeType.GAN_LINK, OrderNodeStatus.CHUA_GIAO),)
    edit_lead = ctx(UnitMemberRole.DUNG, uuid.uuid4(), is_lead=True)
    script_lead = ctx(UnitMemberRole.BIEN_TAP, LEAD_BT, is_lead=True)
    assert OrderActionKind.ASSIGN in kinds(available_actions(linking, unassigned, edit_lead))
    assert OrderActionKind.ASSIGN not in kinds(available_actions(linking, unassigned, script_lead))
    # The obsolete BTD setting changes nothing.
    flipped = OrderActorContext.from_membership(
        membership(UnitMemberRole.BIEN_TAP, LEAD_BT, is_lead=True),
        UnitSettings(btd_link_attacher=UnitMemberRole.BIEN_TAP),
    )
    assert OrderActionKind.ASSIGN not in kinds(available_actions(linking, unassigned, flipped))


def test_09_the_two_last_gates_belong_to_the_script_lead_and_the_head() -> None:
    # The gates act on the product: the last production node's hand-in.
    link = node(OrderNodeType.DUNG, OrderNodeStatus.HOAN_THANH, assignee=EDITOR)
    nodes = (node(OrderNodeType.BIEN_TAP, OrderNodeStatus.HOAN_THANH), link)
    lead = ctx(UnitMemberRole.BIEN_TAP, LEAD_BT, is_lead=True)
    head = ctx(UnitMemberRole.HEAD, HEAD)
    video_gate = order(OrderStage.DUYET_VIDEO_BT)
    assert kinds(available_actions(video_gate, nodes, lead)) == {
        OrderActionKind.APPROVE_VIDEO,
        OrderActionKind.RETURN_VIDEO,
    }
    # The default matrix gives the head every gate, the script lead's included.
    assert kinds(available_actions(video_gate, nodes, head)) == {
        OrderActionKind.APPROVE_VIDEO,
        OrderActionKind.RETURN_VIDEO,
        OrderActionKind.SET_PRIORITY,
        OrderActionKind.CANCEL,
    }
    final_gate = order(OrderStage.FINAL_REVIEW)
    assert kinds(available_actions(final_gate, nodes, head)) == {
        OrderActionKind.APPROVE_FINAL,
        OrderActionKind.RETURN_FINAL,
        OrderActionKind.SET_PRIORITY,
        OrderActionKind.CANCEL,
    }
    assert kinds(available_actions(final_gate, nodes, lead)) == set()
    for action in available_actions(video_gate, nodes, lead):
        assert action.node_id == link.id


def test_10_nothing_moves_a_finished_order() -> None:
    head = ctx(UnitMemberRole.HEAD, HEAD)
    for stage in (OrderStage.COMPLETED, OrderStage.CANCELLED):
        assert available_actions(order(stage), (), head) == ()


def test_11_assert_allowed_tells_a_wrong_stage_from_a_wrong_person() -> None:
    pending = order(OrderStage.ORDER_PENDING)
    nodes = (node(OrderNodeType.BIEN_TAP, OrderNodeStatus.CHUA_TOI),)
    head = ctx(UnitMemberRole.HEAD, HEAD)
    writer = ctx(UnitMemberRole.BIEN_TAP, EDITOR)
    action = assert_allowed(OrderActionKind.APPROVE_ORDER, pending, nodes, head)
    assert action.label == "Duyệt order" and action.requires_note is False
    assert assert_allowed(OrderActionKind.RETURN_ORDER, pending, nodes, head).requires_note is True
    with pytest.raises(OrderActionNotAllowedError):
        assert_allowed(OrderActionKind.APPROVE_ORDER, pending, nodes, writer)
    with pytest.raises(OrderStateError):
        assert_allowed(OrderActionKind.APPROVE_FINAL, pending, nodes, head)
    with pytest.raises(OrderStateError):
        assert_allowed(OrderActionKind.SET_PRIORITY, order(OrderStage.COMPLETED), nodes, head)


# --- the flexible process (0044) ---------------------------------------------------


def test_12_every_process_plans_exactly_its_ticked_nodes_in_pipeline_order() -> None:
    from meobot.domain.orders.labels import video_type_label
    from meobot.domain.orders.models import needs_design_link, process_code, production_nodes

    b, t, d = OrderNodeType.BIEN_TAP, OrderNodeType.THIET_KE, OrderNodeType.DUNG
    expected = {
        "B": ((b,), "Biên kịch"),
        "T": ((t,), "Design"),
        "D": ((d,), "Dựng"),
        "BT": ((b, t), f"Biên kịch{SEP}Design"),
        "BD": ((b, d), f"Biên kịch{SEP}Dựng"),
        "TD": ((t, d), f"Design{SEP}Dựng"),
        "BTD": ((b, t, d), f"Biên kịch{SEP}Design{SEP}Dựng"),
    }
    # The form's words, joined by a single right-pointing angle quote.
    assert [ord(char) for char in SEP] == [0x20, 0x203A, 0x20]
    assert {code.value for code in OrderVideoType} == set(expected)
    for code_value, (nodes, label) in expected.items():
        code = OrderVideoType(code_value)
        assert NODE_PLAN[code] == nodes, code
        assert production_nodes(code) == nodes
        assert first_node(code) is nodes[0]
        assert next_node(code, nodes[-1]) is None
        assert video_type_label(code) == label
        # Ticked in any order, the code is the same.
        assert process_code(reversed(nodes)) is code
        assert process_code(nodes) is code
        statuses = initial_node_statuses(code)
        for node_type in (b, t, d):
            assert statuses[node_type] is (
                OrderNodeStatus.CHUA_TOI if node_type in nodes else OrderNodeStatus.BO_QUA
            )
        assert set(statuses) == {b, t, d}
        # An edit without a design node needs the design with the order.
        assert needs_design_link(code) is (d in nodes and t not in nodes)
    assert process_code([]) is None
    with pytest.raises(ValueError):
        process_code([OrderNodeType.GAN_LINK])
    day = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
    assert format_order_code("TUAN", OrderVideoType.BD, day, 1) == "TUAN-BD-261008-01"


def test_13_the_last_production_node_hands_in_the_product_on_every_process() -> None:
    from meobot.domain.orders.models import last_production_node

    expected = {
        OrderVideoType.B: OrderNodeType.BIEN_TAP,
        OrderVideoType.T: OrderNodeType.THIET_KE,
        OrderVideoType.BT: OrderNodeType.THIET_KE,
        OrderVideoType.BD: OrderNodeType.DUNG,
        OrderVideoType.TD: OrderNodeType.DUNG,
        OrderVideoType.D: OrderNodeType.DUNG,
        OrderVideoType.BTD: OrderNodeType.DUNG,
    }
    for code, last in expected.items():
        assert last_production_node(code) is last, code
    # The design lead hands out a legacy link node of a design-last order only.
    design_lead = ctx(UnitMemberRole.THIET_KE, uuid.uuid4(), is_lead=True)
    unassigned = (node(OrderNodeType.GAN_LINK, OrderNodeStatus.CHUA_GIAO),)
    for code, allowed in (
        (OrderVideoType.T, True),
        (OrderVideoType.BT, True),
        (OrderVideoType.TD, False),
    ):
        linking = order(OrderStage.GAN_LINK, video_type=code)
        assert (
            OrderActionKind.ASSIGN in kinds(available_actions(linking, unassigned, design_lead))
        ) is allowed


def test_14_the_final_review_is_the_orderers_and_the_head_only_stands_in() -> None:
    link = node(OrderNodeType.DUNG, OrderNodeStatus.HOAN_THANH, assignee=EDITOR)
    final_gate = order(OrderStage.FINAL_REVIEW)
    orderer = ctx(UnitMemberRole.ORDERER, OWNER)
    assert {OrderActionKind.APPROVE_FINAL, OrderActionKind.RETURN_FINAL} <= kinds(
        available_actions(final_gate, (link,), orderer)
    )
    action = assert_allowed(OrderActionKind.APPROVE_FINAL, final_gate, (link,), orderer)
    assert action.node_id == link.id
    # Another marketer may not decide somebody else's order.
    other = ctx(UnitMemberRole.ORDERER, uuid.uuid4())
    assert kinds(available_actions(final_gate, (link,), other)) == set()
    with pytest.raises(OrderActionNotAllowedError):
        assert_allowed(OrderActionKind.APPROVE_FINAL, final_gate, (link,), other)
    # The head holds FINAL_REVIEW by default: a stand-in.
    head = ctx(UnitMemberRole.HEAD, HEAD)
    assert OrderActionKind.APPROVE_FINAL in kinds(available_actions(final_gate, (link,), head))
    # Without the permission, the head may not.
    no_stand_in = OrderActorContext.from_membership(
        membership(UnitMemberRole.HEAD, HEAD),
        UnitSettings(permissions={"HEAD": {"FINAL_REVIEW": "NONE"}}),
    )
    assert OrderActionKind.APPROVE_FINAL not in kinds(
        available_actions(final_gate, (link,), no_stand_in)
    )
    # The orderer decides only at the final gate, not the order gate.
    pending = order(OrderStage.ORDER_PENDING)
    assert OrderActionKind.APPROVE_ORDER not in kinds(available_actions(pending, (link,), orderer))


def test_15_the_script_leads_video_review_follows_the_script_node() -> None:
    from meobot.domain.orders.pipeline import video_review_applies

    on = UnitSettings(review_video_by_script_lead=True)
    off = UnitSettings()
    for code in OrderVideoType:
        has_script = "B" in code.value
        assert video_review_applies(on, code) is has_script, code
        assert video_review_applies(off, code) is False
