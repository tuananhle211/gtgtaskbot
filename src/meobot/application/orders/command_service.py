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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.account.member_code import write_member_code
from meobot.application.audit_service import AuditService
from meobot.application.board.holders import ads_approvers
from meobot.application.orders.code_service import OrderCodeService
from meobot.application.orders.design_code_service import (
    DesignCodeService,
    has_birthday_orders,
    manages_design_codes,
)
from meobot.application.orders.node_assignees import OrderNodeAssigneeService
from meobot.application.orders.notifications import OrderNotificationService
from meobot.application.orders.scope import OrderScope
from meobot.application.orders.script_review import (
    PASSED_SCRIPTS,
    OrderScriptReviewer,
    PassedScripts,
    ScriptVerdict,
)
from meobot.application.orders.token_ledger import OrderTokenLedgerService
from meobot.application.orders.work_recorder import OrderWorkRecorder
from meobot.application.org.seats import load_seats, points_department
from meobot.application.pr_support import lock_row, record_pr_event
from meobot.application.units.directory import ROLE_FOR_NODE, UnitDirectoryService
from meobot.core.config import Settings
from meobot.core.time import ensure_utc, utcnow
from meobot.db.models.order import (
    Order,
    OrderApproval,
    OrderEvent,
    OrderNode,
    OrderNodeAssignee,
    OrderNodePoints,
    OrderSubmission,
)
from meobot.db.models.org_unit import OrgUnitMember, UnitDuration, UnitPlatform, UnitVideoKind
from meobot.db.models.user import User
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.models import Actor
from meobot.domain.orders.deadlines import (
    WORK_TIMEZONE,
    describe_overrun,
    overrun,
    parse_tokens,
)
from meobot.domain.orders.design_codes import (
    MAX_REFERENCE_LINKS,
    DesignItem,
    DesignPick,
    describe_items,
    items_from_json,
    items_to_json,
    join_links,
    same_items,
    split_links,
    total_points,
)
from meobot.domain.orders.errors import (
    OrderActionNotAllowedError,
    OrderNotFoundError,
    OrderStaleVersionError,
    OrderStateError,
    OrderValidationError,
)
from meobot.domain.orders.labels import RETURN_REASON_LABELS, node_type_label
from meobot.domain.orders.models import (
    ACTIVE_NODE_STATUSES,
    NODE_PLAN,
    TERMINAL_STAGES,
    BirthdayGender,
    OrderApprovalDecision,
    OrderApprovalGate,
    OrderCategory,
    OrderEventKind,
    OrderNodeStatus,
    OrderNodeType,
    OrderReturnReason,
    OrderScriptSource,
    OrderStage,
    OrderVideoType,
    is_design_only,
    last_production_node,
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
from meobot.domain.orders.task_codes import (
    TASK_PHASES,
    TaskFormSpec,
    describe_change,
    form_specs,
    phase_label,
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
    #: 0053: the orderer's wished finish. Required.
    desired_deadline_at: datetime | None = None
    #: 0056: the Trưởng phòng ADS chosen to approve this order. None = the
    #: orderer's own head, else every head.
    approver_user_id: uuid.UUID | None = None
    #: The design codes suggested for the Design step (the older field; see
    #: ``task_codes``).
    design_codes: tuple[DesignPick, ...] | None = None
    #: 0074: the task codes suggested for each ticked step ("Task công việc"),
    #: by node type - all optional. The hand-ins say what was actually done.
    task_codes: Mapping[OrderNodeType, Sequence[DesignPick]] | None = None


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
    desired_deadline_at: datetime | None = None
    approver_user_id: uuid.UUID | None = None
    #: 0074: new suggested task codes, by node type (the whole set replaces
    #: the old one).
    task_codes: Mapping[OrderNodeType, Sequence[DesignPick]] | None = None


@dataclass(frozen=True, slots=True)
class BirthdayCommand:
    """A birthday order ("Order sinh nhật") as the form sends it. On an edit,
    ``None`` leaves a field as it is."""

    full_name: str | None = None
    branch: str | None = None
    gender: str | None = None
    birthday_on: date | None = None
    #: "Ảnh minh hoạ": the photos the designer works from. ``reference_links``
    #: is the whole list; ``reference_link`` the older single field (either).
    reference_link: str | None = None
    reference_links: Sequence[str] | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class NodePlan:
    """What a Leader puts on a node (0053): tokens and a deadline. On a node
    that was sent back they are this round's revision tokens and deadline."""

    tokens: Decimal | float | str | None = None
    deadline_at: datetime | None = None


@dataclass(slots=True)
class _Loaded:
    order: Order
    nodes: dict[OrderNodeType, OrderNode]
    scope: OrderScope
    settings: UnitSettings
    membership: UnitMembership
    #: 0069: a Design node's open assignees (node_id → rows). Empty for a
    #: single-assignee node, whose NodeView keeps using ``assignee_user_id``.
    design_assignees: dict[uuid.UUID, list[OrderNodeAssignee]] = field(default_factory=dict)

    def view(self) -> OrderView:
        return OrderView(
            id=self.order.id,
            video_type=self.order.video_type,
            stage=self.order.stage,
            owner_user_id=self.order.owner_user_id,
            is_priority=self.order.is_priority,
            category=self.order.category,
            approved=self.order.order_approved_at is not None,
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
                assignee_user_ids=frozenset(
                    row.user_id for row in self.design_assignees.get(node.id, ())
                ),
                accepted_user_ids=frozenset(
                    row.user_id
                    for row in self.design_assignees.get(node.id, ())
                    if row.accepted_at is not None
                ),
                submitted_user_ids=frozenset(
                    row.user_id
                    for row in self.design_assignees.get(node.id, ())
                    if row.submitted_at is not None
                ),
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
        script_reviewer: OrderScriptReviewer | None = None,
        passed_scripts: PassedScripts = PASSED_SCRIPTS,
    ) -> None:
        self._session = session
        self._settings = settings
        self._audit = audit
        self._directory = directory
        self._codes = codes
        self._notify = notifications
        self._kpi = kpi
        self._tokens = OrderTokenLedgerService(session)
        self._design = DesignCodeService(session, directory=directory, audit=audit)
        #: 0069: several designers on one Design node, each scored on their list.
        self._assignees = OrderNodeAssigneeService(session)
        #: The AI reading of "Khung nhập kịch bản"; None = nobody to ask.
        self._script_reviewer = script_reviewer
        self._passed_scripts = passed_scripts

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
        self._validate_fields(command.title, command.order_content)
        desired = _desired_deadline(command.desired_deadline_at, now=utcnow())
        # Only Design ticked: no ORD approval - the order goes straight to the
        # Design step, to the Design Leader to hand out - and its "Task công
        # việc" (design codes) stands for the video kind, platform, duration.
        design_only = is_design_only(command.video_type)
        # Somebody who approves orders needs no approval of their own order -
        # and so no approver.
        own_gate = context.permissions.holds(AdsPermission.ORDER_APPROVE)
        approver = (
            None
            if own_gate or design_only
            else await self._approver(unit.id, command.approver_user_id, actor.user_id)
        )
        if not own_gate and not design_only and approver is None:
            # Nobody chosen: the orderer's own approvers (0061). None at all -
            # no head of theirs approves orders - and they must pick one.
            routes = await ads_approvers(self._session, unit.id)
            if not routes.default_approvers(actor.user_id):
                raise OrderValidationError(
                    "Trưởng phòng của bạn không có quyền duyệt order: hãy chọn người duyệt.",
                    details={"reason": "approver_required", "field": "approver_user_id"},
                )
        await self._validate_preassigned(unit.id, command.video_type, command.preassigned)
        kind = platform = duration = None
        ai_note = None
        # 0074: what the orderer suggests for each ticked step - all optional;
        # the workers' hand-ins say what was actually done.
        suggestions = await self._suggestions(
            unit.id,
            settings,
            command.video_type,
            _task_picks(command.task_codes, command.design_codes),
        )
        design_items = suggestions.get(OrderNodeType.THIET_KE, ())
        if not design_only:
            # "Loại video" and "Thời lượng" left the form (0074); kept if sent.
            kind = await self._video_kind(unit.id, command.video_kind_id, required=False)
            platform = await self._platform(unit.id, command.platform_id, required=True)
            duration = await self._duration(unit.id, command.duration_id, required=False)
            # Last check, before anything is written: the AI reads the script.
            # A script that breaks the rules goes back to the orderer here. A
            # Design-only order's "Nội dung" is a plain brief: not read.
            ai_note = await self._review_script(settings, command.title, command.order_content)

        if entry is None or entry.member_code is None:
            # The order code no longer carries it (2026-10-20), but a person
            # who orders still gets one, as before.
            await self._assign_member_code(unit.id, actor, tagged=entry is not None)

        now = utcnow()
        code = await self._codes.allocate()
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
            script_source=None if design_only else command.script_source,
            design_link=_blank_to_none(command.design_link),
            reference_link=_blank_to_none(command.reference_link),
            source_link=_blank_to_none(command.source_link),
            note=_blank_to_none(command.note),
            owner_user_id=actor.user_id,
            stage=OrderStage.ORDER_PENDING,
            submitted_at=now,
            desired_deadline_at=desired,
            approver_user_id=approver,
            design_items=items_to_json(design_items) if design_items else None,
            suggested_task_items=_suggested_json(suggestions),
        )
        self._session.add(order)
        await self._session.flush()
        created: dict[OrderNodeType, OrderNode] = {}
        for node_type, status in initial_node_statuses(command.video_type).items():
            own = suggestions.get(node_type) if node_type is not OrderNodeType.THIET_KE else None
            created[node_type] = OrderNode(
                order_id=order.id,
                node_type=node_type,
                status=status,
                preassigned_user_id=command.preassigned.get(node_type),
                task_items=items_to_json(own) if own else None,
            )
            self._session.add(created[node_type])
        await self._session.flush()
        await self._event(order, OrderEventKind.SUBMITTED, actor, note=ai_note)
        await self._audit_order(actor, request_id, AuditAction.ORDER_SUBMITTED, order, before=None)
        if design_only and not own_gate:
            # No ORD gate: it starts at Design as it is placed. The Design
            # Leader stands in for the approver - handing the step out or
            # taking it approves the order (:meth:`_design_only_approval`),
            # cancelling it turns it down.
            loaded = _Loaded(
                order=order,
                nodes=created,
                scope=OrderScope.for_actor(membership, settings, unit_id=unit.id),
                settings=settings,
                membership=membership,
            )
            await self._activate(loaded, created[OrderNodeType.THIET_KE], actor, now, first=True)
            self._bump(order)
            await self._session.flush()
            return order
        if own_gate:
            # Placed by a head: approved as it is placed, straight to the
            # first step. Nobody is asked to approve it.
            return await self.approve_order(
                actor=actor,
                request_id=request_id,
                order_id=order.id,
                expected_version=order.version,
            )
        await self._notify.order_submitted(actor=actor, order=order, unit=settings)
        return order

    # --- birthday orders (0058) ---------------------------------------------------

    async def create_birthday(
        self, *, actor: Actor, request_id: uuid.UUID, command: BirthdayCommand
    ) -> Order:
        """Place an "Order sinh nhật": one person's birthday artwork, straight
        to the Design step. Nobody approves it first and Design's hand-in
        finishes it. Placed by the Design team, or by a member granted the
        birthday-order board (``BIRTHDAY``, :func:`has_birthday_orders`)."""
        membership = await self._directory.require(actor, UnitCode.ADS)
        entry = membership.entry(UnitCode.ADS)
        unit = await self._directory.unit(UnitCode.ADS)
        settings = UnitSettings.model_validate(unit.settings or {})
        scope = OrderScope.for_actor(membership, settings, unit_id=unit.id)
        if actor.user_id is None or not has_birthday_orders(membership, settings):
            raise OrderActionNotAllowedError(
                "Bạn chưa có quyền tạo order sinh nhật.",
                details={"reason": "birthday_forbidden"},
            )
        name = _birthday_name(command.full_name)
        branch = _birthday_branch(command.branch)
        gender = _birthday_gender(command.gender)
        if command.birthday_on is None:
            raise OrderValidationError(
                "Cần nhập ngày sinh.",
                details={"reason": "birthday_date_required", "field": "birthday_on"},
            )
        note = _blank_to_none(command.note)
        if entry is None or entry.member_code is None:
            await self._assign_member_code(unit.id, actor, tagged=entry is not None)
        # 2026-10-23: a birthday card is Design's T1 x1 - scored when done.
        birthday_items = (
            await self._design.birthday_items(unit.id) if settings.design_codes_enabled else ()
        )
        now = utcnow()
        code = await self._codes.allocate()
        order = Order(
            unit_id=unit.id,
            code=code,
            title=name,
            video_type=OrderVideoType.T,
            category=OrderCategory.BIRTHDAY,
            design_items=items_to_json(birthday_items) if birthday_items else None,
            suggested_task_items=_suggested_json({OrderNodeType.THIET_KE: birthday_items}),
            order_content=note or f"Thiết kế sinh nhật: {name}",
            design_link=_birthday_links(command),
            note=note,
            owner_user_id=actor.user_id,
            stage=OrderStage.ORDER_PENDING,
            submitted_at=now,
            # No gate before Design: it starts as it is placed.
            order_approved_at=now,
            birthday_branch=branch,
            birthday_gender=gender,
            birthday_on=command.birthday_on,
        )
        self._session.add(order)
        await self._session.flush()
        nodes: dict[OrderNodeType, OrderNode] = {}
        for node_type, status in initial_node_statuses(order.video_type).items():
            nodes[node_type] = OrderNode(order_id=order.id, node_type=node_type, status=status)
            self._session.add(nodes[node_type])
        await self._session.flush()
        await self._event(order, OrderEventKind.SUBMITTED, actor)
        await self._audit_order(actor, request_id, AuditAction.ORDER_SUBMITTED, order, before=None)
        loaded = _Loaded(
            order=order, nodes=nodes, scope=scope, settings=settings, membership=membership
        )
        await self._activate(loaded, nodes[first_node(order.video_type)], actor, now, first=True)
        self._bump(order)
        await self._session.flush()
        return order

    async def edit_birthday(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        order_id: uuid.UUID,
        expected_version: int,
        command: BirthdayCommand,
    ) -> Order:
        """Correct a birthday order's details: whoever placed it, the Design
        Leader, the OWNER or an ADMIN, for as long as it is not finished."""
        loaded = await self._load(actor, order_id, expected_version)
        order = loaded.order
        if order.category is not OrderCategory.BIRTHDAY:
            raise OrderNotFoundError("Không tìm thấy.", details={"reason": "order_not_found"})
        if order.stage in TERMINAL_STAGES:
            raise OrderStateError(
                "Order đã kết thúc, không sửa được nữa.",
                details={"reason": "order_stage_mismatch", "stage": order.stage.value},
            )
        if not (actor.user_id == order.owner_user_id or manages_design_codes(loaded.membership)):
            raise OrderActionNotAllowedError(
                "Bạn không có quyền sửa order sinh nhật này.",
                details={"reason": "birthday_forbidden"},
            )
        before = self._snapshot(order)
        if command.full_name is not None:
            order.title = _birthday_name(command.full_name)
        if command.branch is not None:
            order.birthday_branch = _birthday_branch(command.branch)
        if command.gender is not None:
            order.birthday_gender = _birthday_gender(command.gender)
        if command.birthday_on is not None:
            order.birthday_on = command.birthday_on
        if command.reference_links is not None or command.reference_link is not None:
            order.design_link = _birthday_links(command)
        if command.note is not None:
            order.note = _blank_to_none(command.note)
            order.order_content = order.note or f"Thiết kế sinh nhật: {order.title}"
        self._bump(order)
        await self._session.flush()
        await self._event(order, OrderEventKind.BIRTHDAY_EDITED, actor)
        await self._audit_order(
            actor, request_id, AuditAction.ORDER_BIRTHDAY_EDITED, order, before=before
        )
        return order

    async def check_script(self, *, actor: Actor, title: str, script: str) -> ScriptVerdict | None:
        """ "AI duyệt kịch bản" on the order form, before anything is sent.

        For whoever may place an order. None when the check is switched off
        (or there is no AI): the order may simply be sent. A pass is
        remembered, so sending the same title and script does not ask again.
        """
        membership = await self._directory.require(actor, UnitCode.ADS)
        unit = await self._directory.unit(UnitCode.ADS)
        settings = UnitSettings.model_validate(unit.settings or {})
        context = OrderActorContext.from_membership(membership, settings)
        if actor.user_id is None or not context.permissions.allows(AdsPermission.ORDER_CREATE):
            raise OrderValidationError(
                "Bạn chưa có quyền tạo order trong luồng ORD.",
                details={"reason": "not_an_orderer"},
            )
        if not script.strip():
            raise OrderValidationError(
                "Cần nhập nội dung order.",
                details={"reason": "content_missing", "field": "order_content"},
            )
        if self._script_reviewer is None or not settings.script_ai_review:
            return None
        return await self._ask_ai(settings, title, script)

    async def _ask_ai(self, settings: UnitSettings, title: str, script: str) -> ScriptVerdict:
        rules = settings.script_review_rules
        if self._passed_scripts.passed(title, script, rules):
            return ScriptVerdict(reviewed=True)
        assert self._script_reviewer is not None
        verdict = await self._script_reviewer.review(
            title=title, script=script, rules=rules, footer=settings.script_footer
        )
        if verdict.reviewed and verdict.passed:
            self._passed_scripts.remember(title, script, rules)
        return verdict

    async def _review_script(self, settings: UnitSettings, title: str, script: str) -> str | None:
        """Have the AI read "Khung nhập kịch bản" against the unit's rules -
        or take the pass "AI duyệt kịch bản" got for the very same text.

        Raises ``script_review_failed`` (422) with every offending passage when
        the script breaks a rule: the order is not placed and goes back to the
        orderer. Otherwise returns the line for the order's history - None
        when the check is switched off or there is nobody to ask."""
        if self._script_reviewer is None or not settings.script_ai_review:
            return None
        verdict = await self._ask_ai(settings, title, script)
        if not verdict.passed:
            count = len(verdict.violations)
            raise OrderValidationError(
                f"AI duyệt kịch bản: chưa đạt ({count} lỗi). Sửa các chỗ được chỉ ra rồi gửi lại.",
                details={
                    "reason": "script_review_failed",
                    "field": "order_content",
                    "violations": [violation.to_json() for violation in verdict.violations],
                    "revised_script": verdict.revised_script,
                    "note": verdict.note,
                },
            )
        return verdict.history_note()

    async def _approver(
        self, unit_id: uuid.UUID, chosen: uuid.UUID | None, orderer: uuid.UUID | None
    ) -> uuid.UUID | None:
        """The approver picked on the form: somebody active who approves
        orders (any Trưởng phòng, or whoever holds ORDER_APPROVE), not the
        orderer."""
        if chosen is None:
            return None
        routes = await ads_approvers(self._session, unit_id)
        approvers = {person.user_id for person in (*routes.approver_choices, *routes.head)}
        if chosen not in approvers or chosen == orderer:
            raise OrderValidationError(
                "Người duyệt order phải là người đang có quyền duyệt order.",
                details={"reason": "approver_invalid", "field": "approver_user_id"},
            )
        return chosen

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
        await self._apply_edit(loaded, edit)
        # Sent again = read again: the script may have changed, or the rules.
        ai_note = (
            None
            if is_design_only(order.video_type)
            else await self._review_script(loaded.settings, order.title, order.order_content)
        )
        order.stage = OrderStage.ORDER_PENDING
        order.returned_reason = None
        self._bump(order)
        await self._session.flush()
        await self._event(order, OrderEventKind.RESUBMITTED, actor, note=ai_note)
        await self._audit_order(
            actor, request_id, AuditAction.ORDER_RESUBMITTED, order, before=before
        )
        await self._notify.order_submitted(actor=actor, order=order, unit=loaded.settings)
        return order

    async def edit(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        order_id: uuid.UUID,
        expected_version: int,
        edit: OrderEdit,
    ) -> Order:
        """The orderer fixes a standard order that has not progressed yet
        (before the head's approval, before anyone accepted a step), **without**
        re-submitting it: the order keeps its stage. A returned order is edited
        through :meth:`resubmit`; a birthday order through its own editor."""
        loaded = await self._load(actor, order_id, expected_version)
        assert_allowed(
            OrderActionKind.EDIT, loaded.view(), loaded.node_views(), loaded.scope.context
        )
        order = loaded.order
        before = self._snapshot(order)
        await self._apply_edit(loaded, edit)
        # The script (or the rules) may have changed; re-read it, as on resubmit.
        ai_note = (
            None
            if is_design_only(order.video_type)
            else await self._review_script(loaded.settings, order.title, order.order_content)
        )
        self._bump(order)
        await self._session.flush()
        await self._event(order, OrderEventKind.ORDER_EDITED, actor, note=ai_note)
        await self._audit_order(actor, request_id, AuditAction.ORDER_EDITED, order, before=before)
        # Still waiting for the head: tell them the order they will approve
        # changed. Already in production (a head's own / Design-only order that
        # nobody has accepted): the people on it keep working, no new notice.
        if order.stage is OrderStage.ORDER_PENDING:
            await self._notify.order_submitted(actor=actor, order=order, unit=loaded.settings)
        return order

    async def _apply_edit(self, loaded: _Loaded, edit: OrderEdit) -> None:
        """Apply an :class:`OrderEdit`'s set fields to the order in place, shared
        by :meth:`resubmit` and :meth:`edit`. Validates the fields; never touches
        the stage, the version, events, audit or notifications."""
        order = loaded.order
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
        self._validate_fields(order.title, order.order_content)
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
        if edit.desired_deadline_at is not None:
            order.desired_deadline_at = _desired_deadline(edit.desired_deadline_at, now=utcnow())
        if edit.approver_user_id is not None:
            order.approver_user_id = await self._approver(
                order.unit_id, edit.approver_user_id, order.owner_user_id
            )
        if edit.preassigned is not None:
            await self._validate_preassigned(order.unit_id, order.video_type, edit.preassigned)
            for node_type, node in loaded.nodes.items():
                node.preassigned_user_id = edit.preassigned.get(node_type)
        if edit.task_codes is not None:
            suggestions = await self._suggestions(
                order.unit_id, loaded.settings, order.video_type, edit.task_codes
            )
            order.design_items = items_to_json(suggestions.get(OrderNodeType.THIET_KE, ())) or None
            for node_type, node in loaded.nodes.items():
                if node_type in (OrderNodeType.BIEN_TAP, OrderNodeType.DUNG):
                    node.task_items = items_to_json(suggestions.get(node_type, ())) or None
            order.suggested_task_items = _suggested_json(suggestions)

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
        assignee_user_ids: Sequence[uuid.UUID],
        plan: NodePlan | None = None,
        picks_by_user: Mapping[uuid.UUID, Sequence[DesignPick]] | None = None,
    ) -> Order:
        """Hand the node to one or more members, with its tokens and deadline -
        both required the first time the node is planned (0053).

        A Design node (0069) may be handed to several people at once; the
        Leader may split the design codes per person (``picks_by_user``). Every
        other node takes exactly one person.
        """
        loaded = await self._load(actor, order_id, expected_version)
        node = self._node(loaded, node_id)
        assert_allowed(
            OrderActionKind.ASSIGN,
            loaded.view(),
            loaded.node_views(),
            loaded.scope.context,
            node_id=node.id,
        )
        wanted = list(dict.fromkeys(assignee_user_ids))
        if not wanted:
            raise OrderValidationError(
                "Cần chọn ít nhất một người làm.",
                details={"reason": "assignee_required", "field": "assignee_user_id"},
            )
        multi = self._multi_design(loaded, node)
        if len(wanted) > 1 and not multi:
            raise OrderValidationError(
                "Công đoạn này chỉ giao cho một người.",
                details={"reason": "single_assignee", "field": "assignee_user_id"},
            )
        for user_id in wanted:
            await self._check_function_member(
                loaded.order.unit_id, node.node_type, user_id, video_type=loaded.order.video_type
            )
        before = self._snapshot(loaded.order)
        await self._apply_plan(
            loaded, node, actor, plan or NodePlan(), required=_unplanned(node, loaded.settings)
        )
        await self._design_only_approval(loaded, node, actor)
        if multi:
            items_by_user = await self._resolve_picks_by_user(loaded, picks_by_user)
            rows = await self._assignees.set_people(
                node, wanted, actor=actor, items_by_user=items_by_user
            )
            # The board reads a node's aggregate state off these columns; the
            # per-person state lives in order_node_assignees (0069). The first
            # person is the node's "primary" so a cell still names somebody,
            # and accepted_at mirrors whether anyone has started.
            node.assignee_user_id = wanted[0]
            node.accepted_at = next(
                (row.accepted_at for row in rows if row.accepted_at is not None), None
            )
        else:
            changed = node.assignee_user_id != wanted[0]
            node.assignee_user_id = wanted[0]
            if changed:
                node.accepted_at = None
        node.assigned_at = utcnow()
        if node.status is OrderNodeStatus.CHUA_GIAO:
            node.status = OrderNodeStatus.DANG_LAM
        node.version += 1
        self._bump(loaded.order)
        await self._session.flush()
        await self._event(
            loaded.order, OrderEventKind.NODE_ASSIGNED, actor, node=node, assignee=wanted[0]
        )
        await self._audit_order(
            actor, request_id, AuditAction.ORDER_NODE_ASSIGNED, loaded.order, before=before
        )
        await self._notify.node_assigned(
            actor=actor, order=loaded.order, node=node, unit=loaded.settings
        )
        return loaded.order

    def _multi_design(self, loaded: _Loaded, node: OrderNode) -> bool:
        """Whether this node is a Design node that may carry several people,
        each scored on their own list (0069)."""
        return (
            node.node_type is OrderNodeType.THIET_KE
            and loaded.settings.design_codes_enabled
            and loaded.order.category is OrderCategory.STANDARD
        )

    async def _resolve_picks_by_user(
        self,
        loaded: _Loaded,
        picks_by_user: Mapping[uuid.UUID, Sequence[DesignPick]] | None,
    ) -> dict[uuid.UUID, tuple[DesignItem, ...]]:
        if not picks_by_user:
            return {}
        keep = items_from_json(loaded.order.design_items)
        resolved: dict[uuid.UUID, tuple[DesignItem, ...]] = {}
        for user_id, picks in picks_by_user.items():
            if picks:
                resolved[user_id] = await self._design.resolve(
                    loaded.order.unit_id,
                    picks,
                    keep=keep,
                    phase=OrderNodeType.THIET_KE,
                    forms=self._forms(loaded),
                )
        return resolved

    async def accept(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        order_id: uuid.UUID,
        node_id: uuid.UUID,
        expected_version: int,
        plan: NodePlan | None = None,
    ) -> Order:
        """Take the work. A Leader taking a node routed to them puts its tokens
        and deadline on it here - nobody hands it to them with a plan."""
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
        if self.plans(loaded, node):
            # Only somebody who may plan the node puts a plan on it here; a
            # worker's own accept never changes their tokens.
            await self._apply_plan(
                loaded, node, actor, plan or NodePlan(), required=_unplanned(node, loaded.settings)
            )
        await self._design_only_approval(loaded, node, actor)
        if self._multi_design(loaded, node):
            # Each Design assignee accepts their own row (a Leader taking a
            # routed node becomes one here); the node's own accepted_at mirrors
            # "somebody has started" for the board.
            assert actor.user_id is not None
            await self._assignees.take(node, actor.user_id, actor=actor)
            if node.accepted_at is None:
                node.accepted_at = utcnow()
        else:
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
        design_picks: Sequence[DesignPick] | None = None,
        task_picks: Sequence[DesignPick] | None = None,
    ) -> Order:
        """Hand the work in.

        ``task_picks`` (0074) is the step's own task code(s) - what was
        actually done; required when the order suggested none, and a list
        that differs from the suggestion waits for the phase's Trưởng phòng.
        ``design_picks`` (0058) is what the script step suggests for Design
        (required only while the order has no suggestion for Design); on a
        Design hand-in it stands for ``task_picks``."""
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
        if self._multi_design(loaded, node):
            return await self._submit_design(
                loaded,
                node,
                actor,
                request_id,
                link=link,
                note=note,
                design_picks=task_picks if task_picks is not None else design_picks,
            )
        picked, proposed = await self._design_hand_in(loaded, node, design_picks)
        suggested_own = items_from_json(node.task_items)
        own_set, own_proposed = await self._task_hand_in(loaded, node, task_picks)
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
        if node.node_type is OrderNodeType.THIET_KE:
            node.proposed_design_items = None if proposed is None else items_to_json(proposed)
        elif node.node_type in (OrderNodeType.BIEN_TAP, OrderNodeType.DUNG):
            node.proposed_design_items = (
                None if own_proposed is None else items_to_json(own_proposed)
            )
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
        if picked is not None:
            # The script step's list is the order's from here on.
            loaded.order.design_items = items_to_json(picked)
            await self._event(
                loaded.order,
                OrderEventKind.DESIGN_CODES_SET,
                actor,
                node=node,
                note=f"Mã task design: {describe_items(picked)}",
            )
        if proposed is not None:
            current = items_from_json(loaded.order.design_items)
            await self._event(
                loaded.order,
                OrderEventKind.DESIGN_CODES_PROPOSED,
                actor,
                node=node,
                note=(
                    "Nhân viên yêu cầu thay đổi mã task design: "
                    f"{describe_items(current)} → {describe_items(proposed)}"
                ),
            )
        label = phase_label(node.node_type)
        change: str | None = None
        if own_set is not None:
            # A Trưởng phòng who did the step: their code stands (0074).
            node.task_items = items_to_json(own_set)
            await self._event(
                loaded.order,
                OrderEventKind.DESIGN_CODES_SET,
                actor,
                node=node,
                note=(
                    f"Trưởng phòng tự chốt mã task {label}:\n"
                    f"{describe_change(suggested_own, own_set)}"
                ),
            )
        if own_proposed is not None:
            change = describe_change(suggested_own, own_proposed)
            await self._event(
                loaded.order,
                OrderEventKind.DESIGN_CODES_PROPOSED,
                actor,
                node=node,
                note=f"Nộp khác mã task {label} đề xuất:\n{change}",
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
        birthday = loaded.order.category is OrderCategory.BIRTHDAY
        if (
            proposed is None
            and own_proposed is None
            and (
                # A birthday card (T1 x1, nothing to pick) never waits for the
                # Trưởng phòng: the hand-in finishes it (2026-10-23).
                birthday or not node_needs_review(loaded.settings, node.node_type)
            )
        ):
            # No Leader review for this node (a unit setting): handing in
            # finishes it and the next node starts straight away. Task codes
            # that differ from the suggestion always wait for the Trưởng phòng.
            await self._complete_node(loaded, node, actor, request_id, note=None)
            return loaded.order
        await self._notify.submission_ready(
            actor=actor,
            order=loaded.order,
            node=node,
            unit=loaded.settings,
            approver_node=node.node_type,
            change=change,
        )
        return loaded.order

    async def _submit_design(
        self,
        loaded: _Loaded,
        node: OrderNode,
        actor: Actor,
        request_id: uuid.UUID,
        *,
        link: str | None,
        note: str | None,
        design_picks: Sequence[DesignPick] | None,
    ) -> Order:
        """One Design assignee hands their work in (0069).

        They must say which work items they did (``design_picks``, not empty).
        The node waits until everybody has handed in; then the whole team's
        lists are summed: equal to the order's list finishes the node, a
        different total waits for the Design Leader to approve the change.
        """
        assert actor.user_id is not None
        if not design_picks:
            raise OrderValidationError(
                "Cần chọn đầu việc đã làm trước khi nộp.",
                details={"reason": "work_items_required", "field": "design_codes"},
            )
        keep = items_from_json(loaded.order.design_items)
        items = await self._design.resolve(
            loaded.order.unit_id,
            design_picks,
            keep=keep,
            phase=OrderNodeType.THIET_KE,
            forms=self._forms(loaded),
        )
        if link is None and hands_in_product(loaded.order.video_type, node.node_type):
            raise OrderValidationError(
                "Công đoạn cuối cần nộp link sản phẩm.",
                details={"reason": "link_required", "field": "link"},
            )
        before = self._snapshot(loaded.order)
        submission = await self._submission(node, actor, link=link, script_text=None, note=note)
        await self._assignees.mark_submitted(node, actor.user_id, items, submission.id, actor=actor)
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
        if not await self._assignees.all_submitted(node.id):
            # Still waiting on the rest of the team.
            return loaded.order
        combined = await self._assignees.combined_items(node.id)
        node.submitted_at = utcnow()
        if same_items(combined, keep):
            # The team's total matches the suggestion: finish and move on.
            node.status = OrderNodeStatus.CHO_DUYET
            await self._session.flush()
            await self._complete_node(loaded, node, actor, request_id, note=None)
            return loaded.order
        change = describe_change(keep, combined)
        team = {row.user_id for row in await self._assignees.open_rows(node.id)}
        if team == {actor.user_id} and self._reviews(loaded, node):
            # A Trưởng phòng who did the step alone: their codes stand (0074).
            loaded.order.design_items = items_to_json(combined)
            await self._event(
                loaded.order,
                OrderEventKind.DESIGN_CODES_SET,
                actor,
                node=node,
                note=f"Trưởng phòng tự chốt mã task Design:\n{change}",
            )
            node.status = OrderNodeStatus.CHO_DUYET
            await self._session.flush()
            await self._complete_node(loaded, node, actor, request_id, note=None)
            return loaded.order
        # The total differs: the Design Trưởng phòng decides.
        node.status = OrderNodeStatus.CHO_DUYET
        node.proposed_design_items = items_to_json(combined)
        await self._session.flush()
        await self._event(
            loaded.order,
            OrderEventKind.DESIGN_CODES_PROPOSED,
            actor,
            node=node,
            note=f"Nhóm Design nộp khác mã task đề xuất:\n{change}",
        )
        await self._notify.submission_ready(
            actor=actor,
            order=loaded.order,
            node=node,
            unit=loaded.settings,
            approver_node=node.node_type,
            change=change,
        )
        return loaded.order

    async def set_work_items(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        order_id: uuid.UUID,
        node_id: uuid.UUID,
        expected_version: int,
        design_picks: Sequence[DesignPick],
    ) -> Order:
        """A Design assignee edits their own list of work items before nộp
        (0069). The change is logged for the monthly scoring."""
        loaded = await self._load(actor, order_id, expected_version)
        node = self._node(loaded, node_id)
        assert_allowed(
            OrderActionKind.SET_WORK_ITEMS,
            loaded.view(),
            loaded.node_views(),
            loaded.scope.context,
            node_id=node.id,
        )
        assert actor.user_id is not None
        before = self._snapshot(loaded.order)
        keep = items_from_json(loaded.order.design_items)
        items = await self._design.resolve(
            loaded.order.unit_id,
            design_picks,
            keep=keep,
            phase=OrderNodeType.THIET_KE,
            forms=self._forms(loaded),
        )
        await self._assignees.set_items(node, actor.user_id, items, actor=actor)
        node.version += 1
        self._bump(loaded.order)
        await self._session.flush()
        await self._audit_order(
            actor, request_id, AuditAction.ORDER_NODE_ASSIGNED, loaded.order, before=before
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
        """Approve the hand-in. On a hand-in whose task codes differ from the
        suggestion, the approval makes them the step's: the points are what
        the workers entered (the Trưởng phòng does not edit them; a list that
        is wrong is sent back with "Mã công việc chưa hợp lý")."""
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
        if node.proposed_design_items is not None:
            decided = items_from_json(node.proposed_design_items)
            if node.node_type is OrderNodeType.THIET_KE:
                current = items_from_json(order.design_items)
                order.design_items = items_to_json(decided)
            else:
                current = items_from_json(node.task_items)
                node.task_items = items_to_json(decided)
            node.proposed_design_items = None
            await self._event(
                order,
                OrderEventKind.DESIGN_CODES_SET,
                actor,
                node=node,
                note=(
                    f"Duyệt mã task {phase_label(node.node_type)}:\n"
                    f"{describe_change(current, decided)}"
                ),
            )
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
        node.revision_tokens_pending = False
        node.version += 1
        await self._tokens.record_completion(
            order=order,
            node=node,
            at=now,
            first=first_approval,
            take_tokens=loaded.settings.effort_enabled,
        )
        await self._record_points(loaded, node, now)
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
        if order.category is OrderCategory.BIRTHDAY:
            # Nobody reviews a birthday artwork after Design: it is done.
            order.stage = OrderStage.COMPLETED
            order.completed_at = utcnow()
            await self._session.flush()
            await self._notify.completed(actor=actor, order=order, unit=loaded.settings)
            return
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
        plan: NodePlan | None = None,
        reason: OrderReturnReason | None = None,
    ) -> Order:
        """Send the hand-in back with this round's revision tokens ("Token
        sửa", required, may be 0) and, optionally, a revision deadline.

        ``reason`` (0074, required while task codes are on): "Làm lỗi" and
        "Do thay đổi yêu cầu" open a revision round (counted apart);
        "Mã công việc chưa hợp lý" only asks for the codes again - no revision
        round, no revision tokens."""
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
        reason = _return_reason(loaded.settings, reason)
        codes_only = reason is OrderReturnReason.WRONG_CODES
        plan = plan or NodePlan()
        effort = loaded.settings.effort_enabled and not codes_only
        tokens = _tokens(plan.tokens) if effort else Decimal(0)
        if tokens is None:
            raise OrderValidationError(
                "Cần nhập token sửa (có thể là 0).",
                details={"reason": "tokens_required", "field": "tokens"},
            )
        before = self._snapshot(loaded.order)
        node.status = OrderNodeStatus.DANG_SUA
        if not codes_only:
            node.revision_count += 1
        _count_return(node, reason)
        # A requested change of design codes goes back with the hand-in.
        node.proposed_design_items = None
        if self._multi_design(loaded, node):
            # Everybody's hand-in reopens; their lists are kept to fix.
            await self._assignees.reopen(node, actor=actor)
        node.token_revision = Decimal(node.token_revision or 0) + tokens
        node.revision_tokens_pending = False
        deadline = None if plan.deadline_at is None else _future(plan.deadline_at)
        if deadline is not None:
            node.revision_deadline_at = deadline
        await self._plan_events(
            loaded, node, actor, tokens if effort else None, deadline, revision=True
        )
        node.version += 1
        self._bump(loaded.order)
        await self._session.flush()
        note = _reason_note(reason, note)
        await self._event(
            loaded.order,
            OrderEventKind.NODE_RETURNED,
            actor,
            node=node,
            note=note,
            return_reason=reason,
        )
        await self._audit_order(
            actor, request_id, AuditAction.ORDER_NODE_RETURNED, loaded.order, before=before
        )
        await self._notify.node_returned(
            actor=actor, order=loaded.order, node=node, unit=loaded.settings, note=note, final=False
        )
        return loaded.order

    async def set_node_plan(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        order_id: uuid.UUID,
        node_id: uuid.UUID,
        expected_version: int,
        plan: NodePlan,
    ) -> Order:
        """ "Sửa token/deadline": change a node's plan without handing it out
        again. On a node sent back, the tokens are this round's revision tokens
        (what the Leader enters after the orderer returned the product)."""
        loaded = await self._load(actor, order_id, expected_version)
        node = self._node(loaded, node_id)
        assert_allowed(
            OrderActionKind.SET_NODE_PLAN,
            loaded.view(),
            loaded.node_views(),
            loaded.scope.context,
            node_id=node.id,
        )
        if (
            loaded.settings.effort_enabled
            and node.revision_count > 0
            and plan.tokens is None
            and node.revision_tokens_pending
        ):
            raise OrderValidationError(
                "Cần nhập token sửa (có thể là 0).",
                details={"reason": "tokens_required", "field": "tokens"},
            )
        before = self._snapshot(loaded.order)
        changed = await self._apply_plan(loaded, node, actor, plan, required=False)
        if not changed:
            raise OrderValidationError(
                "Nhập token hoặc deadline cần đổi.",
                details={"reason": "plan_empty", "field": "tokens"},
            )
        node.version += 1
        self._bump(loaded.order)
        await self._session.flush()
        await self._audit_order(
            actor, request_id, AuditAction.ORDER_NODE_ASSIGNED, loaded.order, before=before
        )
        return loaded.order

    def plans(self, loaded: _Loaded, node: OrderNode) -> bool:
        """May the actor of ``loaded`` put tokens and a deadline on ``node``?"""
        managed = function_node(node.node_type, loaded.order.video_type)
        return managed in loaded.scope.context.permissions.nodes(AdsPermission.NODE_ASSIGN)

    async def _apply_plan(
        self,
        loaded: _Loaded,
        node: OrderNode,
        actor: Actor,
        plan: NodePlan,
        *,
        required: bool,
    ) -> bool:
        """Put ``plan`` on ``node``. First pass: ``token_estimate`` /
        ``deadline_at``. A node sent back (``revision_count > 0``): this
        round's revision tokens / ``revision_deadline_at``. Returns whether
        anything was given."""
        # Effort mode off: no tokens are asked for, kept or shown.
        tokens = _tokens(plan.tokens) if loaded.settings.effort_enabled else None
        if required and tokens is None and loaded.settings.effort_enabled:
            raise OrderValidationError(
                "Cần nhập token cho công đoạn.",
                details={"reason": "tokens_required", "field": "tokens"},
            )
        if required and plan.deadline_at is None:
            raise OrderValidationError(
                "Cần đặt deadline cho công đoạn.",
                details={"reason": "deadline_required", "field": "deadline_at"},
            )
        revision = node.revision_count > 0
        current = node.revision_deadline_at if revision else node.deadline_at
        given = None if plan.deadline_at is None else ensure_utc(plan.deadline_at)
        if given is not None and current is not None and given == ensure_utc(current):
            # The dialog sends the deadline back as it was: not a change, and
            # never refused for having passed meanwhile.
            given = None
        if tokens is None and given is None:
            return plan.deadline_at is not None
        deadline = None if given is None else _future(given)
        if revision:
            if tokens is not None:
                taken = await self._tokens.revision_taken(node)
                node.token_revision = taken + tokens
                node.revision_tokens_pending = False
            if deadline is not None:
                node.revision_deadline_at = deadline
        else:
            if tokens is not None:
                node.token_estimate = tokens
            if deadline is not None:
                node.deadline_at = deadline
        await self._plan_events(loaded, node, actor, tokens, deadline, revision=revision)
        return True

    async def _plan_events(
        self,
        loaded: _Loaded,
        node: OrderNode,
        actor: Actor,
        tokens: Decimal | None,
        deadline: datetime | None,
        *,
        revision: bool,
    ) -> None:
        """The plan in the history, and a deadline past the orderer's wish
        counted ("Quá deadline mong muốn: N lần") - a warning, never a refusal."""
        order = loaded.order
        label = node_type_label(node.node_type)
        parts = []
        if tokens is not None:
            parts.append(f"{'Token sửa' if revision else 'Token'} {_format_tokens(tokens)}")
        if deadline is not None:
            parts.append(f"{'Deadline sửa' if revision else 'Deadline'} {_format_when(deadline)}")
        if parts:
            await self._event(
                order,
                OrderEventKind.PLAN_SET,
                actor,
                node=node,
                note=f"{label}: " + " · ".join(parts),
            )
        desired = (
            None if order.desired_deadline_at is None else ensure_utc(order.desired_deadline_at)
        )
        gap = overrun(None if deadline is None else ensure_utc(deadline), desired)
        if gap is not None:
            order.over_deadline_count += 1
            await self._event(
                order,
                OrderEventKind.DEADLINE_EXCEEDED,
                actor,
                node=node,
                note=f"Deadline {label} vượt deadline mong muốn {describe_overrun(gap)}.",
            )

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
        reason: OrderReturnReason | None = None,
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
            reason=_return_reason(loaded.settings, reason),
        )

    async def approve_final(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        order_id: uuid.UUID,
        expected_version: int,
        product_link: str | None = None,
        note: str | None = None,
    ) -> Order:
        """The orderer's last yes. The product is the link the last node handed in
        (``product_link`` only overrides it for the API's older callers)."""
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
        late = overrun(
            now,
            None if order.desired_deadline_at is None else ensure_utc(order.desired_deadline_at),
        )
        if late is not None:
            order.over_deadline_count += 1
            await self._event(
                order,
                OrderEventKind.DEADLINE_EXCEEDED,
                actor,
                note=f"Order hoàn thành sau deadline mong muốn {describe_overrun(late)}.",
            )
        order.product_link = (
            _blank_to_none(product_link)
            or (None if latest is None else latest.link)
            or order.product_link
        )
        comment = _blank_to_none(note)
        await self._approval(
            order,
            OrderApprovalGate.FINAL,
            OrderApprovalDecision.APPROVED,
            actor,
            submission=latest,
            comment=comment,
        )
        await self._event(
            order, OrderEventKind.FINAL_APPROVED, actor, node=node, submission=latest, note=comment
        )
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
        reason: OrderReturnReason | None = None,
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
            reason=_return_reason(loaded.settings, reason),
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
        await self._notify.order_cancelled(
            actor=actor, order=order, unit=loaded.settings, reason=reason
        )
        return order

    # --- shared steps -------------------------------------------------------------------

    async def _design_only_approval(self, loaded: _Loaded, node: OrderNode, actor: Actor) -> None:
        """A Design-only order is approved by the Design Leader's first hand-out
        or take of its step: recorded as the order gate's approval."""
        order = loaded.order
        if (
            order.order_approved_at is not None
            or order.category is not OrderCategory.STANDARD
            or not is_design_only(order.video_type)
            or node.node_type is not OrderNodeType.THIET_KE
        ):
            return
        order.order_approved_at = utcnow()
        order.order_approved_by_user_id = actor.user_id
        await self._approval(order, OrderApprovalGate.ORDER, OrderApprovalDecision.APPROVED, actor)
        await self._event(order, OrderEventKind.ORDER_APPROVED, actor)

    async def _design_hand_in(
        self, loaded: _Loaded, node: OrderNode, picks: Sequence[DesignPick] | None
    ) -> tuple[tuple[DesignItem, ...] | None, tuple[DesignItem, ...] | None]:
        """What a hand-in does to the order's design codes: ``(set, proposed)``.

        ``set`` - the script step's list, which becomes the order's at once.
        ``proposed`` - a designer's list that differs from the order's: a
        request the Design Leader decides. Both ``None`` when the hand-in
        leaves the codes alone (another step, a birthday order, a designer
        who changed nothing, or a unit that switched design codes off)."""
        order = loaded.order
        if (
            not loaded.settings.design_codes_enabled
            or order.category is not OrderCategory.STANDARD
            or OrderNodeType.THIET_KE not in NODE_PLAN[order.video_type]
            or node.node_type not in (OrderNodeType.BIEN_TAP, OrderNodeType.THIET_KE)
        ):
            return None, None
        current = items_from_json(order.design_items)
        forms = self._forms(loaded)
        if node.node_type is OrderNodeType.BIEN_TAP:
            # The script step suggests what Design makes: required only while
            # the order carries no suggestion for Design (0074).
            if not picks:
                if not current:
                    raise _design_codes_required()
                return None, None
            return (
                await self._design.resolve(
                    order.unit_id, picks, keep=current, phase=OrderNodeType.THIET_KE, forms=forms
                ),
                None,
            )
        if picks is None:
            if not current:
                # No script step picked them: the designer does, here.
                raise _design_codes_required()
            return None, None
        if not picks:
            raise _design_codes_required()
        wanted = await self._design.resolve(
            order.unit_id, picks, keep=current, phase=OrderNodeType.THIET_KE, forms=forms
        )
        return None, (None if current and same_items(wanted, current) else wanted)

    async def _task_hand_in(
        self, loaded: _Loaded, node: OrderNode, picks: Sequence[DesignPick] | None
    ) -> tuple[tuple[DesignItem, ...] | None, tuple[DesignItem, ...] | None]:
        """What a Biên kịch / Dựng hand-in does to the step's own task code
        (0074): ``(set, proposed)``.

        ``set`` - a Trưởng phòng who did the step changed it: it stands.
        ``proposed`` - somebody else's code that differs from the suggestion
        (or a first one where none was suggested): it waits for the phase's
        Trưởng phòng. Both ``None`` when it matches the suggestion, the step
        carries no codes, or task codes are switched off."""
        order = loaded.order
        if (
            not loaded.settings.design_codes_enabled
            or order.category is not OrderCategory.STANDARD
            or node.node_type not in (OrderNodeType.BIEN_TAP, OrderNodeType.DUNG)
        ):
            return None, None
        suggested = items_from_json(node.task_items)
        if not picks:
            if not suggested:
                raise _task_codes_required(node.node_type)
            return None, None
        actual = await self._design.resolve(
            order.unit_id, picks, keep=suggested, phase=node.node_type, forms=self._forms(loaded)
        )
        if suggested and same_items(actual, suggested):
            # The same work; only a note may have changed - keep it.
            node.task_items = items_to_json(actual)
            return None, None
        if self._reviews(loaded, node):
            return actual, None
        return None, actual

    def _reviews(self, loaded: _Loaded, node: OrderNode) -> bool:
        """Does the actor of ``loaded`` review this node's hand-ins (its
        Trưởng phòng, the OWNER, an ADMIN)?"""
        managed = function_node(node.node_type, loaded.order.video_type)
        return managed in loaded.scope.context.permissions.nodes(AdsPermission.NODE_REVIEW)

    @staticmethod
    def _forms(loaded: _Loaded) -> tuple[TaskFormSpec, ...]:
        return form_specs(loaded.settings.task_forms)

    async def _suggestions(
        self,
        unit_id: uuid.UUID,
        settings: UnitSettings,
        video_type: OrderVideoType,
        picks: Mapping[OrderNodeType, Sequence[DesignPick]] | None,
    ) -> dict[OrderNodeType, tuple[DesignItem, ...]]:
        """The suggested task codes of each step the process visits."""
        if not settings.design_codes_enabled or not picks:
            return {}
        forms = form_specs(settings.task_forms)
        plan = NODE_PLAN[video_type]
        suggestions: dict[OrderNodeType, tuple[DesignItem, ...]] = {}
        for phase, chosen in picks.items():
            if phase not in TASK_PHASES or phase not in plan or not chosen:
                continue
            suggestions[phase] = await self._design.resolve(
                unit_id, chosen, phase=phase, forms=forms
            )
        return suggestions

    async def _record_points(self, loaded: _Loaded, node: OrderNode, now: datetime) -> None:
        """0074: the step is done - every person on it earns the points of the
        task codes they did. Done again after a return: the row is updated and
        keeps the month of the first time."""
        order = loaded.order
        if not loaded.settings.design_codes_enabled or node.node_type not in TASK_PHASES:
            return
        # A birthday order scores its T1 x1 like any Design step (2026-10-23).
        people: list[tuple[uuid.UUID, tuple[DesignItem, ...]]] = []
        if node.node_type is OrderNodeType.THIET_KE:
            rows = await self._assignees.open_rows(node.id)
            people = [(row.user_id, items_from_json(row.work_items)) for row in rows]
            if not rows and node.assignee_user_id is not None:
                # A Design step handed out before 0073 (or a birthday order):
                # one person, the order's list.
                items = items_from_json(order.design_items)
                if not items and order.category is OrderCategory.BIRTHDAY:
                    # A birthday order placed before it carried T1: T1 x1 anyway.
                    items = await self._design.birthday_items(order.unit_id)
                people = [(node.assignee_user_id, items)]
        elif node.assignee_user_id is not None:
            people = [(node.assignee_user_id, items_from_json(node.task_items))]
        existing = {
            row.user_id: row
            for row in (
                await self._session.scalars(
                    select(OrderNodePoints).where(OrderNodePoints.node_id == node.id)
                )
            ).all()
        }
        seats = await load_seats(self._session, [user_id for user_id, _ in people])
        for user_id, items in people:
            if not items:
                continue
            row = existing.get(user_id)
            if row is None:
                self._session.add(
                    OrderNodePoints(
                        node_id=node.id,
                        order_id=order.id,
                        user_id=user_id,
                        phase=node.node_type,
                        department_id=points_department(seats.get(user_id, ()), node.node_type),
                        items=items_to_json(items),
                        points=total_points(items),
                        first_completed_at=now,
                        updated_at=now,
                    )
                )
            else:
                row.items = items_to_json(items)
                row.points = total_points(items)
                row.updated_at = now
        await self._session.flush()

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
        node_ids = [node.id for node in nodes]
        rows = (
            await self._session.scalars(
                select(OrderNodeAssignee)
                .where(
                    OrderNodeAssignee.node_id.in_(node_ids),
                    OrderNodeAssignee.removed_at.is_(None),
                )
                .order_by(OrderNodeAssignee.assigned_at, OrderNodeAssignee.id)
            )
        ).all()
        design_assignees: dict[uuid.UUID, list[OrderNodeAssignee]] = {}
        for row in rows:
            design_assignees.setdefault(row.node_id, []).append(row)
        return _Loaded(
            order=order,
            nodes={node.node_type: node for node in nodes},
            scope=scope,
            settings=settings,
            membership=membership,
            design_assignees=design_assignees,
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
        # A Design node the orderer chose a designer for starts with that
        # person as its one assignee; a routed Leader is not an assignee - they
        # hand the work out (0069).
        if self._multi_design(loaded, node) and node.preassigned_user_id is not None:
            await self._assignees.set_people(node, [node.preassigned_user_id], actor=actor)
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
        reason: OrderReturnReason | None = None,
    ) -> Order:
        """A gate returns the product: the last production node reopens for
        the same person ("Đang sửa"), who hands in a new version - or, for
        "Mã công việc chưa hợp lý", only picks the task codes again."""
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
        codes_only = reason is OrderReturnReason.WRONG_CODES
        node.status = OrderNodeStatus.DANG_SUA
        if not codes_only:
            node.revision_count += 1
        _count_return(node, reason)
        # The returner does not plan the node: its Leader enters the revision
        # tokens ("Nhập token sửa"); the worker may start meanwhile.
        node.revision_tokens_pending = loaded.settings.effort_enabled and not codes_only
        if self._multi_design(loaded, node):
            # Every designer hands in again; their lists are kept to fix.
            await self._assignees.reopen(node, actor=actor)
        node.version += 1
        order.stage = stage_for_node(node.node_type)
        note = _reason_note(reason, note)
        await self._approval(
            order, gate, OrderApprovalDecision.RETURNED, actor, submission=latest, comment=note
        )
        await self._event(
            order, event, actor, node=node, submission=latest, note=note, return_reason=reason
        )
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
        return_reason: OrderReturnReason | None = None,
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
                return_reason=return_reason,
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
    def _validate_fields(title: str, content: str) -> None:
        """The two fields every order needs. The design link is optional on
        every process (0060), the edit-only ones included."""
        if not title.strip():
            raise OrderValidationError(
                "Cần nhập tên kịch bản.", details={"reason": "title_missing", "field": "title"}
            )
        if not content.strip():
            raise OrderValidationError(
                "Cần nhập nội dung order.",
                details={"reason": "content_missing", "field": "order_content"},
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
        person = None if actor.user_id is None else await self._session.get(User, actor.user_id)
        if person is not None and person.member_code:
            # 0063: the person's own code, given on the admin screens.
            await write_member_code(self._session, person, person.member_code)
            return person.member_code
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
        # 0068: one code per person, across every account.
        taken.update(
            await self._session.scalars(
                select(User.member_code).where(
                    User.member_code.is_not(None),
                    User.deleted_at.is_(None),
                    User.id != actor.user_id,
                )
            )
        )
        code, suffix = base, 2
        while code in taken:
            code = f"{base[: 12 - len(str(suffix))]}{suffix}"
            suffix += 1
        if tagged and person is not None:
            # Kept, so tomorrow's order carries the same prefix.
            await write_member_code(self._session, person, code)
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


def _unplanned(node: OrderNode, settings: UnitSettings) -> bool:
    """A node nobody has planned yet, on its first pass: no tokens on it -
    or, with effort mode off, no deadline."""
    if node.revision_count > 0:
        return False
    if settings.effort_enabled:
        return node.token_estimate is None
    return node.deadline_at is None


def _tokens(value: Decimal | float | str | None) -> Decimal | None:
    try:
        return parse_tokens(value)
    except ValueError as error:
        raise OrderValidationError(
            "Token phải là số từ 0 đến 999,99.",
            details={"reason": "tokens_invalid", "field": "tokens"},
        ) from error


def _future(value: datetime) -> datetime:
    deadline = ensure_utc(value)
    if deadline <= utcnow():
        raise OrderValidationError(
            "Deadline phải sau thời điểm hiện tại.",
            details={"reason": "deadline_in_past", "field": "deadline_at"},
        )
    return deadline


def _desired_deadline(value: datetime | None, *, now: datetime) -> datetime:
    if value is None:
        raise OrderValidationError(
            "Cần nhập deadline mong muốn.",
            details={"reason": "deadline_required", "field": "desired_deadline_at"},
        )
    desired = ensure_utc(value)
    if desired <= now:
        raise OrderValidationError(
            "Deadline mong muốn phải sau thời điểm hiện tại.",
            details={"reason": "deadline_in_past", "field": "desired_deadline_at"},
        )
    return desired


def _format_tokens(tokens: Decimal) -> str:
    text = f"{tokens:.2f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def _format_when(at: datetime) -> str:
    return f"{at.astimezone(WORK_TIMEZONE):%d/%m/%Y %H:%M}"


def _task_picks(
    task_codes: Mapping[OrderNodeType, Sequence[DesignPick]] | None,
    design_codes: Sequence[DesignPick] | None,
) -> dict[OrderNodeType, Sequence[DesignPick]]:
    """The suggested codes by step; the older ``design_codes`` field counts
    for Design when ``task_codes`` says nothing about it."""
    picks: dict[OrderNodeType, Sequence[DesignPick]] = dict(task_codes or {})
    if design_codes and OrderNodeType.THIET_KE not in picks:
        picks[OrderNodeType.THIET_KE] = design_codes
    return picks


def _suggested_json(
    suggestions: Mapping[OrderNodeType, Sequence[DesignItem]],
) -> dict[str, Any] | None:
    stored = {phase.value: items_to_json(items) for phase, items in suggestions.items() if items}
    return stored or None


def _task_codes_required(phase: OrderNodeType) -> OrderValidationError:
    return OrderValidationError(
        f"Cần chọn mã task {phase_label(phase)} đã làm trước khi nộp.",
        details={"reason": "task_codes_required", "field": "task_codes"},
    )


def _return_reason(
    settings: UnitSettings, reason: OrderReturnReason | None
) -> OrderReturnReason | None:
    """0074: "Không duyệt" names one of three reasons while task codes are on."""
    if reason is None and settings.design_codes_enabled:
        raise OrderValidationError(
            "Chọn lý do không duyệt.",
            details={"reason": "return_reason_required", "field": "return_reason"},
        )
    return reason


def _count_return(node: OrderNode, reason: OrderReturnReason | None) -> None:
    if reason is OrderReturnReason.WORKER_FAULT:
        node.returns_fault += 1
    elif reason is OrderReturnReason.CHANGED_REQUEST:
        node.returns_other += 1


def _reason_note(reason: OrderReturnReason | None, note: str) -> str:
    return note if reason is None else f"{RETURN_REASON_LABELS[reason]}: {note}"


def _design_codes_required() -> OrderValidationError:
    return OrderValidationError(
        "Cần chọn ít nhất một mã task design kèm số lượng.",
        details={"reason": "design_codes_required", "field": "design_codes"},
    )


def _birthday_name(value: str | None) -> str:
    cleaned = " ".join((value or "").split())
    if not cleaned:
        raise OrderValidationError(
            "Cần nhập họ và tên.",
            details={"reason": "birthday_name_missing", "field": "full_name"},
        )
    return cleaned[:300]


def _birthday_branch(value: str | None) -> str:
    cleaned = " ".join((value or "").split())
    if not cleaned:
        raise OrderValidationError(
            "Cần nhập chi nhánh.",
            details={"reason": "birthday_branch_missing", "field": "branch"},
        )
    return cleaned[:120]


def _birthday_links(command: BirthdayCommand) -> str | None:
    """ "Ảnh minh hoạ" as stored: the links one per line."""
    links = (
        command.reference_links
        if command.reference_links is not None
        else ([] if command.reference_link is None else [command.reference_link])
    )
    stored = join_links(links)
    if len(split_links(stored)) > MAX_REFERENCE_LINKS:
        raise OrderValidationError(
            f"Ảnh minh hoạ tối đa {MAX_REFERENCE_LINKS} link.",
            details={"reason": "birthday_links_too_many", "field": "reference_links"},
        )
    return stored


def _birthday_gender(value: str | None) -> BirthdayGender:
    try:
        return BirthdayGender((value or "").strip().upper())
    except ValueError as error:
        raise OrderValidationError(
            "Giới tính không hợp lệ.",
            details={"reason": "birthday_gender_invalid", "field": "gender"},
        ) from error


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


__all__ = ["BirthdayCommand", "CreateOrderCommand", "OrderCommandService", "OrderEdit"]
