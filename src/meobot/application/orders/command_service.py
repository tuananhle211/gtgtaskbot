"""Every write on an Ads order. The only place that moves one.

Each command runs the same way: load the order **with a row lock**, check the
version the caller saw, ask the policy whether this person may do this from
here, apply the change, append the event (and the gate decision where there
is one), audit, notify. All on the caller's session, so the request's one
transaction commits all of it or none.

The pipeline's rules are in :mod:`meobot.domain.orders.pipeline`; this
module only knows about rows. The last production node's hand-in is the
product: it must carry the link, and its completion opens the gates (the
script lead's video review where it applies, then the orderer's final review).
A gate sending the product back reopens that node for the same person.

The invariant "one active node per order" is kept by flushing a node's
completion **before** the next one is activated - the partial unique index
would otherwise see both inside one statement.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.board.holders import ads_approvers
from meobot.application.orders.code_service import OrderCodeService
from meobot.application.orders.notifications import OrderNotificationService
from meobot.application.orders.scope import OrderScope
from meobot.application.orders.work_recorder import OrderWorkRecorder
from meobot.application.pr_support import lock_row, record_pr_event
from meobot.application.units.directory import ROLE_FOR_NODE, UnitDirectoryService
from meobot.core.config import Settings
from meobot.core.time import utcnow
from meobot.db.models.order import Order, OrderApproval, OrderEvent, OrderNode, OrderSubmission
from meobot.db.models.org_unit import OrgUnitMember, UnitDuration, UnitPlatform, UnitVideoKind
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.models import Actor
from meobot.domain.orders.errors import (
    OrderNotFoundError,
    OrderStaleVersionError,
    OrderValidationError,
)
from meobot.domain.orders.models import (
    ACTIVE_NODE_STATUSES,
    OrderApprovalDecision,
    OrderApprovalGate,
    OrderEventKind,
    OrderNodeStatus,
    OrderNodeType,
    OrderScriptSource,
    OrderStage,
    OrderVideoType,
    last_production_node,
    needs_design_link,
)
from meobot.domain.orders.permissions import AdsPermission
from meobot.domain.orders.pipeline import (
    NodeView,
    OrderActionKind,
    OrderActorContext,
    OrderView,
    activation_status,
    assert_allowed,
    first_node,
    function_node,
    hands_in_product,
    initial_node_statuses,
    next_node,
    node_needs_review,
    stage_for_node,
    video_review_applies,
)
from meobot.domain.units.member_code import derive_member_code
from meobot.domain.units.models import UnitCode, UnitMembership, UnitSettings


@dataclass(frozen=True, slots=True)
class CreateOrderCommand:
    title: str
    #: The process code ("Quy trình").
    video_type: OrderVideoType
    order_content: str
    script_source: OrderScriptSource | None = None
    design_link: str | None = None
    reference_link: str | None = None
    source_link: str | None = None
    #: Who the orderer chose for each node, by node type. Empty = Leaders decide.
    preassigned: dict[OrderNodeType, uuid.UUID] = field(default_factory=dict)
    #: The video kind from the unit's catalogue. Required while the unit
    #: offers at least one active kind.
    video_kind_id: uuid.UUID | None = None
    platform_id: uuid.UUID | None = None
    duration_id: uuid.UUID | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class OrderEdit:
    """The fields an orderer may change when resubmitting a returned order."""

    title: str | None = None
    order_content: str | None = None
    script_source: OrderScriptSource | None = None
    design_link: str | None = None
    reference_link: str | None = None
    source_link: str | None = None
    preassigned: dict[OrderNodeType, uuid.UUID] | None = None
    #: A different video kind; its name and points are snapshotted again.
    video_kind_id: uuid.UUID | None = None
    platform_id: uuid.UUID | None = None
    duration_id: uuid.UUID | None = None
    note: str | None = None


@dataclass(slots=True)
class _Loaded:
    order: Order
    nodes: dict[OrderNodeType, OrderNode]
    scope: OrderScope
    settings: UnitSettings
    membership: UnitMembership

    def view(self) -> OrderView:
        return OrderView(
            id=self.order.id,
            video_type=self.order.video_type,
            stage=self.order.stage,
            owner_user_id=self.order.owner_user_id,
            is_priority=self.order.is_priority,
        )

    def node_views(self) -> tuple[NodeView, ...]:
        return tuple(
            NodeView(
                id=node.id,
                node_type=node.node_type,
                status=node.status,
                assignee_user_id=node.assignee_user_id,
                preassigned_user_id=node.preassigned_user_id,
                accepted_at=node.accepted_at,
            )
            for node in self.nodes.values()
        )

    def active(self) -> OrderNode | None:
        for node in self.nodes.values():
            if node.status in ACTIVE_NODE_STATUSES:
                return node
        return None

    def product(self) -> OrderNode:
        """The node whose hand-in is the product: the process's last one."""
        return self.nodes[last_production_node(self.order.video_type)]

    def legacy_link(self) -> OrderNode | None:
        """The old "Gắn link" node, on an order created before it was folded
        into the last production node."""
        return self.nodes.get(OrderNodeType.GAN_LINK)


class OrderCommandService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        audit: AuditService,
        directory: UnitDirectoryService,
        codes: OrderCodeService,
        notifications: OrderNotificationService,
        kpi: OrderWorkRecorder,
    ) -> None:
        self._session = session
        self._settings = settings
        self._audit = audit
        self._directory = directory
        self._codes = codes
        self._notify = notifications
        self._kpi = kpi

    # --- create / resubmit -------------------------------------------------------

    async def create(
        self, *, actor: Actor, request_id: uuid.UUID, command: CreateOrderCommand
    ) -> Order:
        """Create and submit in one step: the code is allocated here."""
        membership = await self._directory.require(actor, UnitCode.ADS)
        entry = membership.entry(UnitCode.ADS)
        unit = await self._directory.unit(UnitCode.ADS)
        settings = UnitSettings.model_validate(unit.settings or {})
        context = OrderActorContext.from_membership(membership, settings)
        if actor.user_id is None or not context.permissions.allows(AdsPermission.ORDER_CREATE):
            raise OrderValidationError(
                "Bạn chưa có quyền tạo order trong luồng ORD.",
                details={"reason": "not_an_orderer"},
            )
        self._validate_fields(
            command.video_type, command.title, command.order_content, command.design_link
        )
        await self._validate_preassigned(unit.id, command.video_type, command.preassigned)
        kind = await self._video_kind(unit.id, command.video_kind_id, required=True)
        platform = await self._platform(unit.id, command.platform_id, required=True)
        duration = await self._duration(unit.id, command.duration_id, required=True)

        member_code = None if entry is None else entry.member_code
        if member_code is None:
            member_code = await self._assign_member_code(unit.id, actor, tagged=entry is not None)

        now = utcnow()
        code = await self._codes.allocate(
            unit_id=unit.id,
            member_code=member_code,
            video_type=command.video_type,
            at=now,
        )
        order = Order(
            unit_id=unit.id,
            code=code,
            title=command.title.strip(),
            video_type=command.video_type,
            video_kind_id=None if kind is None else kind.id,
            video_kind_name=None if kind is None else kind.name,
            video_kind_points=None if kind is None else Decimal(kind.points),
            platform_id=None if platform is None else platform.id,
            platform_name=None if platform is None else platform.name,
            duration_id=None if duration is None else duration.id,
            duration_name=None if duration is None else duration.name,
            duration_points=None if duration is None else Decimal(duration.points),
            order_content=command.order_content.strip(),
            script_source=command.script_source,
            design_link=_blank_to_none(command.design_link),
            reference_link=_blank_to_none(command.reference_link),
            source_link=_blank_to_none(command.source_link),
            note=_blank_to_none(command.note),
            owner_user_id=actor.user_id,
            stage=OrderStage.ORDER_PENDING,
            submitted_at=now,
        )
        self._session.add(order)
        await self._session.flush()
        for node_type, status in initial_node_statuses(command.video_type).items():
            self._session.add(
                OrderNode(
                    order_id=order.id,
                    node_type=node_type,
                    status=status,
                    preassigned_user_id=command.preassigned.get(node_type),
                )
            )
        await self._session.flush()
        await self._event(order, OrderEventKind.SUBMITTED, actor)
        await self._audit_order(actor, request_id, AuditAction.ORDER_SUBMITTED, order, before=None)
        await self._notify.order_submitted(actor=actor, order=order, unit=settings)
        return order

    async def resubmit(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        order_id: uuid.UUID,
        expected_version: int,
        edit: OrderEdit,
    ) -> Order:
        loaded = await self._load(actor, order_id, expected_version)
        assert_allowed(
            OrderActionKind.RESUBMIT, loaded.view(), loaded.node_views(), loaded.scope.context
        )
        order = loaded.order
        before = self._snapshot(order)
        if edit.title is not None:
            order.title = edit.title.strip()
        if edit.order_content is not None:
            order.order_content = edit.order_content.strip()
        if edit.script_source is not None:
            order.script_source = edit.script_source
        if edit.design_link is not None:
            order.design_link = _blank_to_none(edit.design_link)
        if edit.reference_link is not None:
            order.reference_link = _blank_to_none(edit.reference_link)
        if edit.source_link is not None:
            order.source_link = _blank_to_none(edit.source_link)
        self._validate_fields(order.video_type, order.title, order.order_content, order.design_link)
        if edit.video_kind_id is not None:
            kind = await self._video_kind(order.unit_id, edit.video_kind_id, required=False)
            assert kind is not None
            order.video_kind_id = kind.id
            order.video_kind_name = kind.name
            order.video_kind_points = Decimal(kind.points)
        if edit.platform_id is not None:
            plat = await self._platform(order.unit_id, edit.platform_id, required=False)
            assert plat is not None
            order.platform_id = plat.id
            order.platform_name = plat.name
        if edit.duration_id is not None:
            dur = await self._duration(order.unit_id, edit.duration_id, required=False)
            assert dur is not None
            order.duration_id = dur.id
            order.duration_name = dur.name
            order.duration_points = Decimal(dur.points)
        if edit.note is not None:
            order.note = _blank_to_none(edit.note)
        if edit.preassigned is not None:
            await self._validate_preassigned(order.unit_id, order.video_type, edit.preassigned)
            for node_type, node in loaded.nodes.items():
                node.preassigned_user_id = edit.preassigned.get(node_type)
        order.stage = OrderStage.ORDER_PENDING
        order.returned_reason = None
        self._bump(order)
        await self._session.flush()
        await self._event(order, OrderEventKind.RESUBMITTED, actor)
        await self._audit_order(
            actor, request_id, AuditAction.ORDER_RESUBMITTED, order, before=before
        )
        await self._notify.order_submitted(actor=actor, order=order, unit=loaded.settings)
        return order

    # --- the head's order gate -----------------------------------------------------

    async def approve_order(
        self, *, actor: Actor, request_id: uuid.UUID, order_id: uuid.UUID, expected_version: int
    ) -> Order:
        loaded = await self._load(actor, order_id, expected_version)
        assert_allowed(
            OrderActionKind.APPROVE_ORDER, loaded.view(), loaded.node_views(), loaded.scope.context
        )
        order = loaded.order
        before = self._snapshot(order)
        now = utcnow()
        order.order_approved_at = now
        order.order_approved_by_user_id = actor.user_id
        await self._approval(order, OrderApprovalGate.ORDER, OrderApprovalDecision.APPROVED, actor)
        await self._event(order, OrderEventKind.ORDER_APPROVED, actor)
        first = loaded.nodes[first_node(order.video_type)]
        await self._activate(loaded, first, actor, now, first=True)
        self._bump(order)
        await self._session.flush()
        await self._audit_order(actor, request_id, AuditAction.ORDER_APPROVED, order, before=before)
        return order

    async def return_order(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        order_id: uuid.UUID,
        expected_version: int,
        reason: str,
    ) -> Order:
        loaded = await self._load(actor, order_id, expected_version)
        assert_allowed(
            OrderActionKind.RETURN_ORDER, loaded.view(), loaded.node_views(), loaded.scope.context
        )
        reason = _require_note(reason)
        order = loaded.order
        before = self._snapshot(order)
        order.stage = OrderStage.ORDER_RETURNED
        order.returned_reason = reason
        await self._approval(
            order, OrderApprovalGate.ORDER, OrderApprovalDecision.RETURNED, actor, comment=reason
        )
        await self._event(order, OrderEventKind.ORDER_RETURNED, actor, note=reason)
        self._bump(order)
        await self._session.flush()
        await self._audit_order(actor, request_id, AuditAction.ORDER_RETURNED, order, before=before)
        await self._notify.order_returned(
            actor=actor, order=order, unit=loaded.settings, reason=reason
        )
        return order

    # --- the nodes -------------------------------------------------------------------

    async def assign(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        order_id: uuid.UUID,
        node_id: uuid.UUID,
        expected_version: int,
        assignee_user_id: uuid.UUID,
    ) -> Order:
        loaded = await self._load(actor, order_id, expected_version)
        node = self._node(loaded, node_id)
        assert_allowed(
            OrderActionKind.ASSIGN,
            loaded.view(),
            loaded.node_views(),
            loaded.scope.context,
            node_id=node.id,
        )
        await self._check_function_member(
            loaded.order.unit_id,
            node.node_type,
            assignee_user_id,
            video_type=loaded.order.video_type,
        )
        before = self._snapshot(loaded.order)
        changed = node.assignee_user_id != assignee_user_id
        node.assignee_user_id = assignee_user_id
        node.assigned_at = utcnow()
        if changed:
            node.accepted_at = None
        if node.status is OrderNodeStatus.CHUA_GIAO:
            node.status = OrderNodeStatus.DANG_LAM
        node.version += 1
        self._bump(loaded.order)
        await self._session.flush()
        await self._event(
            loaded.order, OrderEventKind.NODE_ASSIGNED, actor, node=node, assignee=assignee_user_id
        )
        await self._audit_order(
            actor, request_id, AuditAction.ORDER_NODE_ASSIGNED, loaded.order, before=before
        )
        await self._notify.node_assigned(
            actor=actor, order=loaded.order, node=node, unit=loaded.settings
        )
        return loaded.order

    async def accept(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        order_id: uuid.UUID,
        node_id: uuid.UUID,
        expected_version: int,
    ) -> Order:
        loaded = await self._load(actor, order_id, expected_version)
        node = self._node(loaded, node_id)
        assert_allowed(
            OrderActionKind.ACCEPT,
            loaded.view(),
            loaded.node_views(),
            loaded.scope.context,
            node_id=node.id,
        )
        before = self._snapshot(loaded.order)
        node.accepted_at = utcnow()
        node.version += 1
        self._bump(loaded.order)
        await self._session.flush()
        await self._event(
            loaded.order, OrderEventKind.NODE_ACCEPTED, actor, node=node, assignee=actor.user_id
        )
        await self._audit_order(
            actor, request_id, AuditAction.ORDER_NODE_ACCEPTED, loaded.order, before=before
        )
        await self._notify.node_accepted(
            actor=actor, order=loaded.order, node=node, unit=loaded.settings
        )
        return loaded.order

    async def submit_work(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        order_id: uuid.UUID,
        node_id: uuid.UUID,
        expected_version: int,
        link: str | None,
        script_text: str | None,
        note: str | None,
    ) -> Order:
        loaded = await self._load(actor, order_id, expected_version)
        node = self._node(loaded, node_id)
        assert_allowed(
            OrderActionKind.SUBMIT_WORK,
            loaded.view(),
            loaded.node_views(),
            loaded.scope.context,
            node_id=node.id,
        )
        link, script_text = _blank_to_none(link), _blank_to_none(script_text)
        if link is None and hands_in_product(loaded.order.video_type, node.node_type):
            # The last node's hand-in is the product the orderer reviews.
            raise OrderValidationError(
                "Công đoạn cuối cần nộp link sản phẩm.",
                details={"reason": "link_required", "field": "link"},
            )
        if link is None and script_text is None:
            raise OrderValidationError(
                "Nộp bài cần có link hoặc nội dung kịch bản.",
                details={"reason": "submission_empty", "field": "link"},
            )
        before = self._snapshot(loaded.order)
        submission = await self._submission(
            node, actor, link=link, script_text=script_text, note=note
        )
        node.status = OrderNodeStatus.CHO_DUYET
        node.submitted_at = utcnow()
        node.version += 1
        self._bump(loaded.order)
        await self._session.flush()
        await self._event(
            loaded.order,
            OrderEventKind.WORK_SUBMITTED,
            actor,
            node=node,
            submission=submission,
            note=note,
        )
        await self._audit_order(
            actor, request_id, AuditAction.ORDER_NODE_SUBMITTED, loaded.order, before=before
        )
        if node.node_type is OrderNodeType.GAN_LINK:
            # A legacy order caught at the old link step: the link is in, the
            # step is over, the gates open. Never counted.
            node.status = OrderNodeStatus.HOAN_THANH
            node.version += 1
            await self._session.flush()
            await self._open_gates(loaded, node, actor, submission)
            self._bump(loaded.order)
            await self._session.flush()
            return loaded.order
        if not node_needs_review(loaded.settings, node.node_type):
            # No Leader review for this node (a unit setting): handing in
            # finishes it and the next node starts straight away.
            await self._complete_node(loaded, node, actor, request_id, note=None)
            return loaded.order
        await self._notify.submission_ready(
            actor=actor,
            order=loaded.order,
            node=node,
            unit=loaded.settings,
            approver_node=node.node_type,
        )
        return loaded.order

    async def approve_node(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        order_id: uuid.UUID,
        node_id: uuid.UUID,
        expected_version: int,
        note: str | None = None,
    ) -> Order:
        loaded = await self._load(actor, order_id, expected_version)
        node = self._node(loaded, node_id)
        assert_allowed(
            OrderActionKind.APPROVE_NODE,
            loaded.view(),
            loaded.node_views(),
            loaded.scope.context,
            node_id=node.id,
        )
        order = loaded.order
        before = self._snapshot(order)
        await self._complete_node(loaded, node, actor, request_id, note=note)
        await self._audit_order(
            actor, request_id, AuditAction.ORDER_NODE_APPROVED, order, before=before
        )
        return order

    async def _complete_node(
        self,
        loaded: _Loaded,
        node: OrderNode,
        actor: Actor,
        request_id: uuid.UUID,
        *,
        note: str | None,
    ) -> None:
        """Finish a production node, count it, and start the next one - or,
        after the last node, open the gates with its hand-in as the product.

        Reached from a Leader's approval, or straight from the hand-in when
        the unit does not review that node.
        """
        order = loaded.order
        now = utcnow()
        first_approval = node.approved_at is None
        node.status = OrderNodeStatus.HOAN_THANH
        node.approved_by_user_id = actor.user_id
        if first_approval:
            node.approved_at = now
        node.version += 1
        # The next node may only become active once this one is no longer.
        await self._session.flush()
        latest = await self._latest_submission(node)
        await self._event(
            order, OrderEventKind.NODE_APPROVED, actor, node=node, submission=latest, note=note
        )
        if first_approval:
            await self._kpi.record_first_approval(
                actor=actor,
                request_id=request_id,
                order=order,
                node=node,
                approved_at=now,
                link=None if latest is None else latest.link,
            )
        following = next_node(order.video_type, node.node_type)
        if following is not None:
            await self._activate(loaded, loaded.nodes[following], actor, now, first=False)
        else:
            await self._open_gates(loaded, node, actor, latest)
        self._bump(order)
        await self._session.flush()

    async def _open_gates(
        self,
        loaded: _Loaded,
        node: OrderNode,
        actor: Actor,
        latest: OrderSubmission | None,
    ) -> None:
        """The product is in: the script lead watches it first where that
        review applies, otherwise it goes straight to the orderer."""
        order = loaded.order
        if latest is not None and latest.link:
            order.product_link = latest.link
        order.stage = (
            OrderStage.DUYET_VIDEO_BT
            if video_review_applies(loaded.settings, order.video_type)
            else OrderStage.FINAL_REVIEW
        )
        await self._session.flush()
        await self._notify.submission_ready(
            actor=actor,
            order=order,
            node=node,
            unit=loaded.settings,
            approver_node=OrderNodeType.BIEN_TAP
            if order.stage is OrderStage.DUYET_VIDEO_BT
            else None,
            to_owner=order.stage is OrderStage.FINAL_REVIEW,
        )

    async def return_node(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        order_id: uuid.UUID,
        node_id: uuid.UUID,
        expected_version: int,
        note: str,
    ) -> Order:
        loaded = await self._load(actor, order_id, expected_version)
        node = self._node(loaded, node_id)
        assert_allowed(
            OrderActionKind.RETURN_NODE,
            loaded.view(),
            loaded.node_views(),
            loaded.scope.context,
            node_id=node.id,
        )
        note = _require_note(note)
        before = self._snapshot(loaded.order)
        node.status = OrderNodeStatus.DANG_SUA
        node.revision_count += 1
        node.version += 1
        self._bump(loaded.order)
        await self._session.flush()
        await self._event(loaded.order, OrderEventKind.NODE_RETURNED, actor, node=node, note=note)
        await self._audit_order(
            actor, request_id, AuditAction.ORDER_NODE_RETURNED, loaded.order, before=before
        )
        await self._notify.node_returned(
            actor=actor, order=loaded.order, node=node, unit=loaded.settings, note=note, final=False
        )
        return loaded.order

    # --- the two last gates ------------------------------------------------------------

    async def approve_video(
        self, *, actor: Actor, request_id: uuid.UUID, order_id: uuid.UUID, expected_version: int
    ) -> Order:
        loaded = await self._load(actor, order_id, expected_version)
        assert_allowed(
            OrderActionKind.APPROVE_VIDEO, loaded.view(), loaded.node_views(), loaded.scope.context
        )
        order = loaded.order
        node = loaded.product()
        before = self._snapshot(order)
        latest = await self._latest_product(loaded)
        order.stage = OrderStage.FINAL_REVIEW
        await self._approval(
            order,
            OrderApprovalGate.VIDEO_BT,
            OrderApprovalDecision.APPROVED,
            actor,
            submission=latest,
        )
        await self._event(order, OrderEventKind.VIDEO_APPROVED, actor, node=node, submission=latest)
        self._bump(order)
        await self._session.flush()
        await self._audit_order(
            actor, request_id, AuditAction.ORDER_VIDEO_APPROVED, order, before=before
        )
        await self._notify.submission_ready(
            actor=actor,
            order=order,
            node=node,
            unit=loaded.settings,
            approver_node=None,
            to_owner=True,
        )
        return order

    async def return_video(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        order_id: uuid.UUID,
        expected_version: int,
        note: str,
    ) -> Order:
        loaded = await self._load(actor, order_id, expected_version)
        assert_allowed(
            OrderActionKind.RETURN_VIDEO, loaded.view(), loaded.node_views(), loaded.scope.context
        )
        note = _require_note(note)
        return await self._send_product_back(
            loaded,
            actor,
            request_id,
            gate=OrderApprovalGate.VIDEO_BT,
            event=OrderEventKind.VIDEO_RETURNED,
            action=AuditAction.ORDER_VIDEO_RETURNED,
            note=note,
            final=False,
        )

    async def approve_final(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        order_id: uuid.UUID,
        expected_version: int,
        product_link: str | None = None,
    ) -> Order:
        loaded = await self._load(actor, order_id, expected_version)
        assert_allowed(
            OrderActionKind.APPROVE_FINAL, loaded.view(), loaded.node_views(), loaded.scope.context
        )
        order = loaded.order
        node = loaded.product()
        before = self._snapshot(order)
        now = utcnow()
        latest = await self._latest_product(loaded)
        legacy = loaded.legacy_link()
        if legacy is not None and legacy.status in ACTIVE_NODE_STATUSES:
            # A legacy order whose link was attached on the old link step.
            legacy.status = OrderNodeStatus.HOAN_THANH
            legacy.approved_by_user_id = actor.user_id
            if legacy.approved_at is None:
                legacy.approved_at = now
            legacy.version += 1
        order.stage = OrderStage.COMPLETED
        order.completed_at = now
        order.product_link = (
            _blank_to_none(product_link)
            or (None if latest is None else latest.link)
            or order.product_link
        )
        await self._approval(
            order, OrderApprovalGate.FINAL, OrderApprovalDecision.APPROVED, actor, submission=latest
        )
        await self._event(order, OrderEventKind.FINAL_APPROVED, actor, node=node, submission=latest)
        self._bump(order)
        await self._session.flush()
        await self._audit_order(
            actor, request_id, AuditAction.ORDER_FINAL_APPROVED, order, before=before
        )
        await self._notify.completed(actor=actor, order=order, unit=loaded.settings)
        return order

    async def return_final(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        order_id: uuid.UUID,
        expected_version: int,
        note: str,
    ) -> Order:
        loaded = await self._load(actor, order_id, expected_version)
        assert_allowed(
            OrderActionKind.RETURN_FINAL, loaded.view(), loaded.node_views(), loaded.scope.context
        )
        note = _require_note(note)
        return await self._send_product_back(
            loaded,
            actor,
            request_id,
            gate=OrderApprovalGate.FINAL,
            event=OrderEventKind.FINAL_RETURNED,
            action=AuditAction.ORDER_FINAL_RETURNED,
            note=note,
            final=True,
        )

    # --- flags ------------------------------------------------------------------------

    async def set_priority(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        order_id: uuid.UUID,
        expected_version: int,
        is_priority: bool,
    ) -> Order:
        loaded = await self._load(actor, order_id, expected_version)
        assert_allowed(
            OrderActionKind.SET_PRIORITY, loaded.view(), loaded.node_views(), loaded.scope.context
        )
        order = loaded.order
        before = self._snapshot(order)
        if order.is_priority == is_priority:
            return order
        order.is_priority = is_priority
        self._bump(order)
        await self._session.flush()
        await self._event(
            order,
            OrderEventKind.PRIORITY_SET if is_priority else OrderEventKind.PRIORITY_CLEARED,
            actor,
        )
        await self._audit_order(
            actor, request_id, AuditAction.ORDER_PRIORITY_CHANGED, order, before=before
        )
        return order

    async def cancel(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        order_id: uuid.UUID,
        expected_version: int,
        reason: str,
    ) -> Order:
        loaded = await self._load(actor, order_id, expected_version)
        assert_allowed(
            OrderActionKind.CANCEL, loaded.view(), loaded.node_views(), loaded.scope.context
        )
        reason = _require_note(reason)
        order = loaded.order
        before = self._snapshot(order)
        order.stage = OrderStage.CANCELLED
        order.cancelled_reason = reason
        self._bump(order)
        await self._session.flush()
        await self._event(order, OrderEventKind.CANCELLED, actor, note=reason)
        await self._audit_order(
            actor, request_id, AuditAction.ORDER_CANCELLED, order, before=before
        )
        return order

    # --- shared steps -------------------------------------------------------------------

    async def _load(self, actor: Actor, order_id: uuid.UUID, expected_version: int) -> _Loaded:
        membership = await self._directory.require(actor, UnitCode.ADS)
        unit = await self._directory.unit(UnitCode.ADS)
        settings = UnitSettings.model_validate(unit.settings or {})
        scope = OrderScope.for_actor(membership, settings, unit_id=unit.id)
        order = await lock_row(self._session, Order, order_id)
        if order is None:
            raise OrderNotFoundError("Không tìm thấy.", details={"reason": "order_not_found"})
        visible = await self._session.scalar(
            scope.apply(select(Order.id).where(Order.id == order.id))
        )
        if visible is None:
            raise OrderNotFoundError("Không tìm thấy.", details={"reason": "order_not_visible"})
        if order.version != expected_version:
            raise OrderStaleVersionError(
                "Order đã được xử lý, vui lòng tải lại.",
                details={
                    "reason": "order_stale_version",
                    "expected": expected_version,
                    "current": order.version,
                },
            )
        nodes = (
            await self._session.scalars(
                select(OrderNode)
                .where(OrderNode.order_id == order.id)
                .order_by(OrderNode.created_at)
            )
        ).all()
        return _Loaded(
            order=order,
            nodes={node.node_type: node for node in nodes},
            scope=scope,
            settings=settings,
            membership=membership,
        )

    @staticmethod
    def _node(loaded: _Loaded, node_id: uuid.UUID) -> OrderNode:
        for node in loaded.nodes.values():
            if node.id == node_id:
                return node
        raise OrderNotFoundError("Không tìm thấy.", details={"reason": "node_not_found"})

    async def _activate(
        self, loaded: _Loaded, node: OrderNode, actor: Actor, now: datetime, *, first: bool
    ) -> None:
        """Bring a node up, assigning it straight away when somebody was chosen.

        Assigned is not accepted: the assignee still presses "Nhận việc"."""
        order = loaded.order
        assignee = node.preassigned_user_id
        if assignee is None:
            # Nobody chosen: the node goes to its Leader (else the head) to
            # hand out, rather than hanging as "Chờ giao". Only a unit with
            # nobody allowed to assign it leaves it unassigned.
            routed = (await ads_approvers(self._session, order.unit_id)).assigner(
                node.node_type, order.video_type
            )
            assignee = None if routed is None else routed.user_id
        node.status = activation_status(assignee is not None)
        node.assignee_user_id = assignee
        node.activated_at = now
        node.assigned_at = now if assignee is not None else None
        node.accepted_at = None
        node.version += 1
        order.stage = stage_for_node(node.node_type)
        await self._session.flush()
        await self._event(order, OrderEventKind.NODE_ACTIVATED, actor, node=node, assignee=assignee)
        await self._notify.node_turn(
            actor=actor, order=order, node=node, unit=loaded.settings, first=first
        )

    async def _send_product_back(
        self,
        loaded: _Loaded,
        actor: Actor,
        request_id: uuid.UUID,
        *,
        gate: OrderApprovalGate,
        event: OrderEventKind,
        action: AuditAction,
        note: str,
        final: bool,
    ) -> Order:
        """A gate returns the product: the last production node reopens for
        the same person ("Đang sửa"), who hands in a new version."""
        order = loaded.order
        node = loaded.product()
        before = self._snapshot(order)
        latest = await self._latest_product(loaded)
        legacy = loaded.legacy_link()
        if legacy is not None and legacy.status in ACTIVE_NODE_STATUSES:
            # The old link step is over for good: the product node takes it
            # from here. Flushed first - one active node per order.
            legacy.status = OrderNodeStatus.BO_QUA
            legacy.version += 1
            await self._session.flush()
        node.status = OrderNodeStatus.DANG_SUA
        node.revision_count += 1
        node.version += 1
        order.stage = stage_for_node(node.node_type)
        await self._approval(
            order, gate, OrderApprovalDecision.RETURNED, actor, submission=latest, comment=note
        )
        await self._event(order, event, actor, node=node, submission=latest, note=note)
        self._bump(order)
        await self._session.flush()
        await self._audit_order(actor, request_id, action, order, before=before)
        await self._notify.node_returned(
            actor=actor, order=order, node=node, unit=loaded.settings, note=note, final=final
        )
        return order

    async def _submission(
        self,
        node: OrderNode,
        actor: Actor,
        *,
        link: str | None,
        script_text: str | None,
        note: str | None,
    ) -> OrderSubmission:
        assert actor.user_id is not None
        node.submission_count += 1
        submission = OrderSubmission(
            node_id=node.id,
            submission_no=node.submission_count,
            submitted_by_user_id=actor.user_id,
            link=link,
            script_text=script_text,
            note=_blank_to_none(note),
        )
        self._session.add(submission)
        await self._session.flush()
        return submission

    async def _latest_product(self, loaded: _Loaded) -> OrderSubmission | None:
        """The newest hand-in carrying a link on the product node (or on a
        legacy link node): what the gates review and the order delivers."""
        node_ids = [loaded.product().id]
        legacy = loaded.legacy_link()
        if legacy is not None:
            node_ids.append(legacy.id)
        latest: OrderSubmission | None = await self._session.scalar(
            select(OrderSubmission)
            .where(OrderSubmission.node_id.in_(node_ids), OrderSubmission.link.is_not(None))
            .order_by(OrderSubmission.created_at.desc(), OrderSubmission.submission_no.desc())
            .limit(1)
        )
        return latest

    async def _latest_submission(self, node: OrderNode) -> OrderSubmission | None:
        latest: OrderSubmission | None = await self._session.scalar(
            select(OrderSubmission)
            .where(OrderSubmission.node_id == node.id)
            .order_by(OrderSubmission.submission_no.desc())
            .limit(1)
        )
        return latest

    async def _approval(
        self,
        order: Order,
        gate: OrderApprovalGate,
        decision: OrderApprovalDecision,
        actor: Actor,
        *,
        submission: OrderSubmission | None = None,
        comment: str | None = None,
    ) -> None:
        assert actor.user_id is not None
        previous = (
            await self._session.scalars(
                select(OrderApproval.round_no).where(
                    OrderApproval.order_id == order.id, OrderApproval.gate == gate
                )
            )
        ).all()
        self._session.add(
            OrderApproval(
                order_id=order.id,
                gate=gate,
                round_no=len(previous) + 1,
                decision=decision,
                actor_user_id=actor.user_id,
                submission_id=None if submission is None else submission.id,
                comment=comment,
            )
        )
        await self._session.flush()

    async def _event(
        self,
        order: Order,
        kind: OrderEventKind,
        actor: Actor,
        *,
        node: OrderNode | None = None,
        assignee: uuid.UUID | None = None,
        submission: OrderSubmission | None = None,
        note: str | None = None,
    ) -> None:
        assert actor.user_id is not None
        self._session.add(
            OrderEvent(
                order_id=order.id,
                node_id=None if node is None else node.id,
                kind=kind,
                actor_user_id=actor.user_id,
                assignee_user_id=assignee,
                submission_id=None if submission is None else submission.id,
                note=_blank_to_none(note),
            )
        )
        await self._session.flush()

    async def _audit_order(
        self,
        actor: Actor,
        request_id: uuid.UUID,
        action: AuditAction,
        order: Order,
        *,
        before: dict[str, object] | None,
    ) -> None:
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=action,
            entity_type="order",
            entity_id=order.id,
            before=before,
            after=self._snapshot(order),
        )

    @staticmethod
    def _snapshot(order: Order) -> dict[str, object]:
        return {
            "code": order.code,
            "stage": order.stage.value,
            "version": order.version,
            "is_priority": order.is_priority,
        }

    @staticmethod
    def _bump(order: Order) -> None:
        order.version += 1

    @staticmethod
    def _validate_fields(
        video_type: OrderVideoType, title: str, content: str, design_link: str | None
    ) -> None:
        if not title.strip():
            raise OrderValidationError(
                "Cần nhập tên kịch bản.", details={"reason": "title_missing", "field": "title"}
            )
        if not content.strip():
            raise OrderValidationError(
                "Cần nhập nội dung order.",
                details={"reason": "content_missing", "field": "order_content"},
            )
        if needs_design_link(video_type) and _blank_to_none(design_link) is None:
            raise OrderValidationError(
                "Quy trình có Dựng nhưng không có Design bắt buộc có link thiết kế.",
                details={"reason": "design_link_required", "field": "design_link"},
            )

    async def _video_kind(
        self, unit_id: uuid.UUID, kind_id: uuid.UUID | None, *, required: bool
    ) -> UnitVideoKind | None:
        """The chosen video kind: active and the unit's own. ``None`` only when
        nothing was chosen and the unit offers no active kind (or the caller
        does not require one)."""
        if kind_id is None:
            if not required:
                return None
            offered = await self._session.scalar(
                select(UnitVideoKind.id)
                .where(UnitVideoKind.unit_id == unit_id, UnitVideoKind.active.is_(True))
                .limit(1)
            )
            if offered is not None:
                raise OrderValidationError(
                    "Cần chọn loại video.",
                    details={"reason": "video_kind_required", "field": "video_kind_id"},
                )
            return None
        kind = await self._session.get(UnitVideoKind, kind_id)
        if kind is None or kind.unit_id != unit_id or not kind.active:
            raise OrderValidationError(
                "Loại video không hợp lệ hoặc đã ngừng dùng.",
                details={
                    "reason": "video_kind_invalid",
                    "field": "video_kind_id",
                    "value": str(kind_id),
                },
            )
        return kind

    async def _platform(
        self, unit_id: uuid.UUID, platform_id: uuid.UUID | None, *, required: bool
    ) -> UnitPlatform | None:
        if platform_id is None:
            if not required:
                return None
            offered = await self._session.scalar(
                select(UnitPlatform.id)
                .where(UnitPlatform.unit_id == unit_id, UnitPlatform.active.is_(True))
                .limit(1)
            )
            if offered is not None:
                raise OrderValidationError(
                    "Cần chọn nền tảng.",
                    details={"reason": "platform_required", "field": "platform_id"},
                )
            return None
        row = await self._session.get(UnitPlatform, platform_id)
        if row is None or row.unit_id != unit_id or not row.active:
            raise OrderValidationError(
                "Nền tảng không hợp lệ hoặc đã ngừng dùng.",
                details={
                    "reason": "platform_invalid",
                    "field": "platform_id",
                    "value": str(platform_id),
                },
            )
        return row

    async def _duration(
        self, unit_id: uuid.UUID, duration_id: uuid.UUID | None, *, required: bool
    ) -> UnitDuration | None:
        if duration_id is None:
            if not required:
                return None
            offered = await self._session.scalar(
                select(UnitDuration.id)
                .where(UnitDuration.unit_id == unit_id, UnitDuration.active.is_(True))
                .limit(1)
            )
            if offered is not None:
                raise OrderValidationError(
                    "Cần chọn thời lượng.",
                    details={"reason": "duration_required", "field": "duration_id"},
                )
            return None
        row = await self._session.get(UnitDuration, duration_id)
        if row is None or row.unit_id != unit_id or not row.active:
            raise OrderValidationError(
                "Thời lượng không hợp lệ hoặc đã ngừng dùng.",
                details={
                    "reason": "duration_invalid",
                    "field": "duration_id",
                    "value": str(duration_id),
                },
            )
        return row

    async def _assign_member_code(self, unit_id: uuid.UUID, actor: Actor, *, tagged: bool) -> str:
        """A code for an orderer nobody gave one, made from their name.

        The admin screen is where codes are assigned; this keeps a head or the
        owner from being stopped at their first order by a blank field. The
        code is written back onto the tag when there is one, so tomorrow's
        order carries the same prefix. The owner acting without a tag has no
        row to write to, and gets the same deterministic code each time.
        """
        base = derive_member_code(actor.full_name)
        taken = set(
            await self._session.scalars(
                select(OrgUnitMember.member_code).where(
                    OrgUnitMember.unit_id == unit_id,
                    OrgUnitMember.left_at.is_(None),
                    OrgUnitMember.member_code.is_not(None),
                    OrgUnitMember.user_id != actor.user_id,
                )
            )
        )
        code, suffix = base, 2
        while code in taken:
            code = f"{base[: 12 - len(str(suffix))]}{suffix}"
            suffix += 1
        if tagged and actor.user_id is not None:
            row = await self._directory.member(unit_id, actor.user_id)
            if row is not None:
                row.member_code = code
                await self._session.flush()
        return code

    async def _validate_preassigned(
        self,
        unit_id: uuid.UUID,
        video_type: OrderVideoType,
        preassigned: dict[OrderNodeType, uuid.UUID],
    ) -> None:
        planned = set(initial_node_statuses(video_type))
        for node_type, user_id in preassigned.items():
            if node_type is OrderNodeType.GAN_LINK or node_type not in planned:
                raise OrderValidationError(
                    "Chỉ chọn sẵn người cho các công đoạn có trong loại video.",
                    details={"reason": "preassign_not_in_plan", "node_type": node_type.value},
                )
            if initial_node_statuses(video_type)[node_type] is OrderNodeStatus.BO_QUA:
                raise OrderValidationError(
                    "Loại video này không đi qua công đoạn đó.",
                    details={"reason": "preassign_not_in_plan", "node_type": node_type.value},
                )
            await self._check_function_member(unit_id, node_type, user_id, video_type=video_type)

    async def _check_function_member(
        self,
        unit_id: uuid.UUID,
        node_type: OrderNodeType,
        user_id: uuid.UUID,
        *,
        video_type: OrderVideoType,
    ) -> None:
        """The person must be an active Ads member of the node's function."""
        wanted = ROLE_FOR_NODE[function_node(node_type, video_type)]
        rows = await self._directory.members(unit_id, role=wanted)
        if not any(row.user.id == user_id for row in rows):
            raise OrderValidationError(
                "Người được chọn không thuộc đúng bộ phận của luồng ORD.",
                details={
                    "reason": "not_a_unit_function_member",
                    "node_type": node_type.value,
                    "user_id": str(user_id),
                },
            )


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _require_note(value: str | None) -> str:
    note = _blank_to_none(value)
    if note is None:
        raise OrderValidationError(
            "Cần ghi lý do.", details={"reason": "note_required", "field": "note"}
        )
    return note


__all__ = ["CreateOrderCommand", "OrderCommandService", "OrderEdit"]
