"""One task, read: the same page for a PR content item and an Ads order.

Nothing here decides anything. Each unit's half is read through the services
that unit's own routes use, so the unified page can never show more, or
offer more, than the unit's own screen would:

* **Ads** - :meth:`OrderQueryService.detail` (the order scope, the actions
  from the pipeline policy) and the board's :class:`AdsBoardSource` row for
  the step strip;
* **PR** - :meth:`PrQueryService.get_content` (PR's read permission),
  :meth:`PrAvailableActionService.for_content` (the matrix, the gates, the
  capabilities), the approval, undo and production histories, and the board's
  :class:`PrBoardSource` row for the step strip.

The unit wall comes first: a person outside the task's unit is told
"không tìm thấy", exactly as the unit's own routes would tell them, and the
OWNER sees both units.

Action keys are opaque to the client and parsed back only by
:mod:`meobot.application.tasks.action_service`: ``ads:<KIND>[:<node id>]``
and ``pr:<KIND>[:<argument>]``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.board.holders import (
    AdsApprovers,
    People,
    ads_approvers,
    format_names,
    pr_production_assigners,
    pr_reviewers,
)
from meobot.application.board.sources import (
    PR_STAGE_LABELS,
    AdsBoardSource,
    PrBoardSource,
    ads_kind_label,
    display_names,
    format_points,
)
from meobot.application.orders.query_service import OrderDetail
from meobot.application.orders.services import OrderServices
from meobot.application.pr_action_service import (
    PrActionKind,
    PrAvailableAction,
)
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_query_service import ContentDetail
from meobot.application.pr_services import PrServices
from meobot.application.tasks.sync import build_task
from meobot.application.units.directory import ROLE_FOR_NODE, UnitDirectoryService
from meobot.core.config import Settings
from meobot.core.errors import NotFoundError
from meobot.core.time import ensure_utc, utcnow
from meobot.db.models.order import Order
from meobot.db.models.pr import PrApprovalEvent, PrContentItem
from meobot.db.models.pr_content_version import PrContentVersion
from meobot.db.models.pr_production import PrProductionSubmission
from meobot.db.models.pr_transition import PrContentTransitionEvent
from meobot.db.models.task import SOURCE_PR_CONTENT, Task
from meobot.db.models.user import User
from meobot.domain.board.models import PHASE_LABELS, Phase, TaskRow
from meobot.domain.identity.models import Actor, Role
from meobot.domain.orders.labels import (
    event_label,
    node_type_label,
    video_type_label,
)
from meobot.domain.orders.models import (
    OrderApprovalDecision,
    OrderEventKind,
    OrderNodeStatus,
    OrderNodeType,
    OrderStage,
    OrderVideoType,
)
from meobot.domain.orders.pipeline import OrderActionKind, function_node, hands_in_product
from meobot.domain.pr.models import (
    PrApprovalDecision,
    PrApprovalStage,
    PrContentType,
    PrPriority,
    PrProductionArtifactType,
    PrWorkflowStage,
)
from meobot.domain.pr.policy import PrCapability
from meobot.domain.pr.workflow import STAGE_APPROVAL_GATES, PrTransitionTrigger
from meobot.domain.units.labels import UNIT_LABELS, UNIT_SHORT_LABELS
from meobot.domain.units.models import UnitCode

Emphasis = Literal["PRIMARY", "SECONDARY", "DANGER"]
FieldType = Literal["text", "longtext", "link", "date"]
FieldGroup = Literal["common", "pr", "ads"]

#: PR's words, the same ones the PR screens use (``frontend/src/lib/labels.ts``).
PR_CONTENT_TYPE_LABELS: dict[PrContentType, str] = {
    PrContentType.ULTRA_SHORT_SCRIPT: "Kịch bản siêu ngắn",
    PrContentType.SHORT_VIDEO_SCRIPT: "Kịch bản video ngắn",
    PrContentType.FACEBOOK_POST: "Bài đăng Facebook",
    PrContentType.LONG_YOUTUBE_SCRIPT: "Kịch bản YouTube dài",
    PrContentType.PRESS_ARTICLE: "Báo chí",
    PrContentType.CORPORATE_TVC: "TVC doanh nghiệp",
}
PR_PRIORITY_LABELS: dict[PrPriority, str] = {
    PrPriority.NORMAL: "Bình thường",
    PrPriority.HIGH: "Ưu tiên",
    PrPriority.URGENT: "Gấp",
    PrPriority.CRITICAL: "Rất gấp",
}
PR_TRANSITION_LABELS: dict[PrWorkflowStage, str] = {
    PrWorkflowStage.BRIEFING: "Chuyển sang Brief",
    PrWorkflowStage.SCRIPTING: "Bắt đầu viết kịch bản",
    PrWorkflowStage.AI_REVIEW: "Gửi đi AI review",
    PrWorkflowStage.TEAM_LEAD_REVIEW: "Gửi duyệt Trưởng nhóm",
    PrWorkflowStage.PRODUCTION: "Bắt đầu sản xuất",
    PrWorkflowStage.INTERNAL_REVIEW: "Gửi duyệt nội bộ",
    PrWorkflowStage.ARCHIVED: "Lưu trữ nội dung",
    PrWorkflowStage.CANCELLED: "Hủy nội dung",
}
PR_DECISION_LABELS: dict[PrApprovalDecision, str] = {
    PrApprovalDecision.APPROVED: "Duyệt",
    PrApprovalDecision.REVISION_REQUIRED: "Yêu cầu sửa",
    PrApprovalDecision.REJECTED: "Từ chối",
}
PR_GATE_LABELS: dict[PrApprovalStage, str] = {
    PrApprovalStage.TEAM_LEAD_REVIEW: "Trưởng nhóm duyệt",
    PrApprovalStage.HEAD_REVIEW: "Trưởng phòng duyệt",
    PrApprovalStage.INTERNAL_REVIEW: "Duyệt nội bộ",
}
PR_UNDO_LABELS: dict[str, str] = {
    "UNDO_TEAM_LEAD_APPROVAL": "Hoàn tác duyệt Trưởng nhóm",
    "UNDO_HEAD_APPROVAL": "Hoàn tác duyệt Trưởng phòng",
    "UNDO_INTERNAL_REVIEW": "Hoàn tác duyệt nội bộ",
    "UNDO_REVISION": "Hoàn tác yêu cầu sửa",
}
#: The PR action kinds the unified page drives. Everything else (resources,
#: derivatives, destinations, publications, comments, versions, AI review)
#: stays on the PR endpoints and the PR-only tabs.
PR_WORKFLOW_KINDS: frozenset[PrActionKind] = frozenset(
    {
        PrActionKind.TRANSITION,
        PrActionKind.APPROVAL,
        PrActionKind.SET_PRIORITY,
        PrActionKind.ASSIGN_PRODUCER,
        PrActionKind.CLAIM_PRODUCTION,
        PrActionKind.START_PRODUCTION,
        PrActionKind.SUBMIT_PRODUCTION,
        PrActionKind.UNDO_LAST_ACTION,
    }
)
#: Ads actions whose key carries the node they act on.
ADS_NODE_KINDS: frozenset[OrderActionKind] = frozenset(
    {
        OrderActionKind.ASSIGN,
        OrderActionKind.ACCEPT,
        OrderActionKind.SUBMIT_WORK,
        OrderActionKind.APPROVE_NODE,
        OrderActionKind.RETURN_NODE,
    }
)
_ADS_PRIMARY: frozenset[OrderActionKind] = frozenset(
    {
        OrderActionKind.RESUBMIT,
        OrderActionKind.APPROVE_ORDER,
        OrderActionKind.ACCEPT,
        OrderActionKind.SUBMIT_WORK,
        OrderActionKind.APPROVE_NODE,
        OrderActionKind.APPROVE_VIDEO,
        OrderActionKind.APPROVE_FINAL,
    }
)
_ADS_DANGER: frozenset[OrderActionKind] = frozenset({OrderActionKind.CANCEL})
_EMPHASIS_ORDER = {"PRIMARY": 0, "SECONDARY": 1, "DANGER": 2}


class TaskNotFoundError(NotFoundError):
    """No such task, or one behind a wall - the same sentence for both."""

    code = "task_not_found"


def not_found(reason: str) -> TaskNotFoundError:
    return TaskNotFoundError("Không tìm thấy.", details={"reason": reason})


# --- the shape -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PersonRef:
    user_id: uuid.UUID | None
    name: str


@dataclass(frozen=True, slots=True)
class TaskSource:
    type: str
    id: uuid.UUID


@dataclass(frozen=True, slots=True)
class TaskSummary:
    id: uuid.UUID
    unit: str
    unit_label: str
    code: str
    title: str
    kind: str
    kind_label: str
    phase: str
    phase_label: str
    stage: str
    stage_label: str
    #: What the task waits on, as the board's row ``state`` (``CHO_DUYET``,
    #: ``CHO_PHAN_CONG``, ``DA_GIAO``, ``CHUA_GIAO``...); screens colour by it.
    state: str | None
    owner: PersonRef
    current_person: PersonRef | None
    is_priority: bool
    urgent: bool
    created_at: datetime
    updated_at: datetime
    stage_since: datetime | None
    finished_at: datetime | None
    product_link: str | None
    latest_link: str | None
    revisions: int
    version: int
    source: TaskSource
    #: The chip tag: "PR" / "ORD".
    unit_short_label: str = ""


@dataclass(frozen=True, slots=True)
class TaskStep:
    key: str
    label: str
    person_name: str | None
    status: str
    status_label: str
    is_current: bool
    since: datetime | None
    revisions: int


@dataclass(frozen=True, slots=True)
class TaskPerson:
    role_label: str
    user_id: uuid.UUID | None
    name: str


@dataclass(frozen=True, slots=True)
class TaskField:
    key: str
    label: str
    value: str | None
    type: FieldType
    group: FieldGroup


@dataclass(frozen=True, slots=True)
class TaskSubmission:
    id: uuid.UUID
    label: str
    step_label: str
    person_name: str | None
    link: str | None
    text: str | None
    note: str | None
    submitted_at: datetime
    status_label: str | None


@dataclass(frozen=True, slots=True)
class TaskTimelineEntry:
    at: datetime
    actor_name: str | None
    label: str
    note: str | None


@dataclass(frozen=True, slots=True)
class TaskActionView:
    key: str
    label: str
    emphasis: Emphasis
    requires_note: bool
    inputs: tuple[str, ...] = ()
    assignee_options: tuple[PersonRef, ...] = ()
    #: The inputs that may not be left empty (beyond a required note).
    required_inputs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TaskDetail:
    task: TaskSummary
    steps: tuple[TaskStep, ...] = ()
    people: tuple[TaskPerson, ...] = ()
    fields: tuple[TaskField, ...] = ()
    submissions: tuple[TaskSubmission, ...] = ()
    timeline: tuple[TaskTimelineEntry, ...] = ()
    actions: tuple[TaskActionView, ...] = field(default_factory=tuple)


# --- the service ---------------------------------------------------------------


class TaskDetailService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        directory: UnitDirectoryService,
        pr: PrServices,
        orders: OrderServices,
    ) -> None:
        self._session = session
        self._settings = settings
        self._directory = directory
        self._pr = pr
        self._orders = orders

    # --- resolving -------------------------------------------------------------

    async def resolve(self, actor: Actor, ref: str) -> Task:
        """The task ``ref`` names, if this person's unit may see it.

        ``ref`` is the task's id or code, or its source's id. A source that
        has no task yet (written by raw SQL after the backfill) gets one here.
        """
        task = await self._find(ref)
        if task is None:
            raise not_found("task_not_found")
        unit = unit_of(task)
        membership = await self._directory.membership_for(actor)
        if not membership.has(unit):
            raise not_found("unit_not_visible")
        return task

    async def _find(self, ref: str) -> Task | None:
        raw = ref.strip()
        if not raw:
            return None
        source: PrContentItem | Order | None = None
        try:
            ident = uuid.UUID(raw)
        except ValueError:
            task: Task | None = await self._session.scalar(
                select(Task).where(or_(Task.code == raw, Task.code == raw.upper()))
            )
            if task is not None:
                return task
            source = await self._session.scalar(
                select(PrContentItem).where(PrContentItem.code == raw)
            ) or await self._session.scalar(select(Order).where(Order.code == raw.upper()))
        else:
            task = await self._session.scalar(
                select(Task).where(
                    or_(Task.id == ident, Task.pr_content_id == ident, Task.order_id == ident)
                )
            )
            if task is not None:
                return task
            source = await self._session.get(PrContentItem, ident) or await self._session.get(
                Order, ident
            )
        if source is None:
            return None
        built = build_task(source, utcnow())
        if built is None:
            return None
        self._session.add(built)
        await self._session.flush()
        return built

    # --- reading ---------------------------------------------------------------

    async def detail(self, actor: Actor, ref: str) -> TaskDetail:
        task = await self.resolve(actor, ref)
        if task.source_type == SOURCE_PR_CONTENT:
            assert task.pr_content_id is not None
            return await self._pr_detail(actor, task, task.pr_content_id)
        assert task.order_id is not None
        return await self._ads_detail(actor, task, task.order_id)

    # --- Ads -------------------------------------------------------------------

    async def _ads_detail(self, actor: Actor, task: Task, order_id: uuid.UUID) -> TaskDetail:
        queries = self._orders.queries
        detail = await queries.detail(actor, str(order_id))
        scoped = await queries.scoped(actor)
        order = detail.order
        source = AdsBoardSource(
            self._session, self._settings, scope=scoped.scope, unit_settings=scoped.settings
        )
        nodes_by_order = {order.id: list(detail.nodes)}
        links = await source._latest_links(nodes_by_order)
        names = dict(detail.names)
        approvers = await ads_approvers(self._session, order.unit_id)
        row = source._row(order, list(detail.nodes), names, links, utcnow(), approvers)
        current = _current_person(row)
        summary = TaskSummary(
            id=task.id,
            unit=UnitCode.ADS.value,
            unit_label=UNIT_LABELS[UnitCode.ADS],
            unit_short_label=UNIT_SHORT_LABELS[UnitCode.ADS],
            code=order.code,
            title=order.title,
            kind=order.video_type.value,
            kind_label=ads_kind_label(order),
            phase=row.phase.value,
            phase_label=row.phase_label,
            stage=order.stage.value,
            stage_label=row.status_label,
            state=row.state,
            owner=PersonRef(order.owner_user_id, names.get(order.owner_user_id, "")),
            current_person=current,
            is_priority=order.is_priority,
            urgent=detail.urgent,
            created_at=ensure_utc(order.submitted_at),
            updated_at=ensure_utc(order.updated_at),
            stage_since=row.stage_since,
            finished_at=None if task.finished_at is None else ensure_utc(task.finished_at),
            product_link=order.product_link,
            latest_link=row.latest_link,
            revisions=row.revisions,
            version=order.version,
            source=TaskSource(type="ORDER", id=order.id),
        )
        return TaskDetail(
            task=summary,
            steps=_steps(row),
            people=_ads_people(detail, approvers),
            fields=_ads_fields(order),
            submissions=_ads_submissions(detail),
            timeline=_ads_timeline(detail),
            actions=await self._ads_actions(detail),
        )

    async def _ads_actions(self, detail: OrderDetail) -> tuple[TaskActionView, ...]:
        order = detail.order
        nodes = {node.id: node for node in detail.nodes}
        views: list[TaskActionView] = []
        for action in detail.actions:
            kind = action.kind
            key = f"ads:{kind.value}"
            node = nodes.get(action.node_id) if action.node_id is not None else None
            if kind in ADS_NODE_KINDS and action.node_id is not None:
                key = f"{key}:{action.node_id}"
            label = action.label
            if node is not None and kind in ADS_NODE_KINDS:
                label = f"{action.label} · {node_type_label(node.node_type)}"
            if kind is OrderActionKind.SET_PRIORITY:
                label = "Bỏ ưu tiên" if order.is_priority else "Đánh dấu ưu tiên"
            emphasis: Emphasis = (
                "PRIMARY"
                if kind in _ADS_PRIMARY
                else ("DANGER" if kind in _ADS_DANGER else "SECONDARY")
            )
            inputs: tuple[str, ...] = ()
            required: tuple[str, ...] = ()
            options: tuple[PersonRef, ...] = ()
            if kind is OrderActionKind.ASSIGN and node is not None:
                inputs = ("assignee",)
                options = await self._ads_assignees(order.unit_id, node.node_type, order.video_type)
                if node.status is OrderNodeStatus.CHUA_GIAO:
                    emphasis = "PRIMARY"
            elif kind is OrderActionKind.SUBMIT_WORK:
                inputs = (
                    ("text", "link", "note")
                    if node is not None and node.node_type is OrderNodeType.BIEN_TAP
                    else ("link", "note")
                )
                if node is not None and hands_in_product(order.video_type, node.node_type):
                    # The last node hands in the product: its link is required.
                    label = f"Nộp sản phẩm · {node_type_label(node.node_type)}"
                    required = ("link",)
            elif kind is OrderActionKind.APPROVE_FINAL:
                inputs = ("link",)
            elif kind in (
                OrderActionKind.RETURN_ORDER,
                OrderActionKind.RETURN_NODE,
                OrderActionKind.RETURN_VIDEO,
                OrderActionKind.RETURN_FINAL,
                OrderActionKind.CANCEL,
                OrderActionKind.APPROVE_NODE,
            ):
                inputs = ("note",)
            views.append(
                TaskActionView(
                    key=key,
                    label=label,
                    emphasis=emphasis,
                    requires_note=action.requires_note,
                    inputs=inputs,
                    assignee_options=options,
                    required_inputs=required,
                )
            )
        return tuple(sorted(views, key=lambda view: _EMPHASIS_ORDER[view.emphasis]))

    async def _ads_assignees(
        self,
        unit_id: uuid.UUID,
        node_type: OrderNodeType,
        video_type: OrderVideoType,
    ) -> tuple[PersonRef, ...]:
        wanted = ROLE_FOR_NODE[function_node(node_type, video_type)]
        rows = await self._directory.members(unit_id, role=wanted)
        return tuple(PersonRef(row.user.id, row.user.full_name) for row in rows)

    # --- PR --------------------------------------------------------------------

    async def _pr_detail(self, actor: Actor, task: Task, content_id: uuid.UUID) -> TaskDetail:
        pr = self._pr
        detail = await pr.queries.get_content(actor=actor, content_id=content_id)
        content = detail.content
        if sa_inspect(content).expired_attributes:
            # A PR write in this request (an action posted through the task)
            # flushed the row and expired its server-set columns; reading one
            # lazily would be IO outside the async context.
            await self._session.refresh(content)
        submissions = list(await pr.production.list_submissions(content.id))
        approvals = list(await pr.approvals.history(content.id))
        transitions = list(await pr.undo.history(content.id))
        versions = list(await pr.queries.list_content_versions(actor=actor, content_id=content.id))
        user_ids: set[uuid.UUID] = {content.owner_user_id, content.created_by_user_id}
        if content.producer_user_id is not None:
            user_ids.add(content.producer_user_id)
        user_ids.update(item.submitted_by_user_id for item in submissions)
        user_ids.update(item.reviewer_user_id for item in approvals)
        user_ids.update(item.actor_user_id for item in transitions if item.actor_user_id)
        user_ids.update(item.created_by_user_id for item in versions)
        names = await display_names(self._session, user_ids)
        brands = {detail.brand.id: detail.brand.name} if detail.brand is not None else {}
        board = PrBoardSource(self._session, self._settings, pr.queries)
        now = utcnow()
        reviewers = await pr_reviewers(self._session, [content])
        assigners = (
            await pr_production_assigners(self._session)
            if content.producer_user_id is None
            and content.workflow_stage in (PrWorkflowStage.APPROVED, PrWorkflowStage.PRODUCTION)
            else ()
        )
        row = board._row(content, names, brands, now, reviewers, assigners)
        latest = submissions[0] if submissions else None
        latest_link = (
            latest.location
            if latest is not None and latest.artifact_type is not PrProductionArtifactType.NAS_PATH
            else None
        )
        phase = row.phase
        version = detail.current_version_no or 0
        owner = PersonRef(content.owner_user_id, names.get(content.owner_user_id, ""))
        current = _current_person(row)
        created = ensure_utc(content.created_at)
        summary = TaskSummary(
            id=task.id,
            unit=UnitCode.PR.value,
            unit_label=UNIT_LABELS[UnitCode.PR],
            unit_short_label=UNIT_SHORT_LABELS[UnitCode.PR],
            code=content.code,
            title=content.title,
            kind=content.content_type.value if content.content_type else "",
            kind_label=PR_CONTENT_TYPE_LABELS.get(content.content_type, "Chưa phân loại")
            if content.content_type
            else "Chưa phân loại",
            phase=phase.value,
            phase_label=PHASE_LABELS[phase],
            stage=content.workflow_stage.value,
            stage_label=row.status_label,
            state=row.state,
            owner=owner,
            current_person=current,
            is_priority=row.is_priority,
            urgent=row.urgent,
            created_at=created,
            updated_at=ensure_utc(content.updated_at) if content.updated_at else created,
            stage_since=ensure_utc(task.stage_since) if task.stage_since else row.stage_since,
            finished_at=None if task.finished_at is None else ensure_utc(task.finished_at),
            product_link=latest_link if phase is Phase.DONE else None,
            latest_link=latest_link,
            revisions=sum(
                1 for item in approvals if item.decision is PrApprovalDecision.REVISION_REQUIRED
            ),
            version=version,
            source=TaskSource(type=SOURCE_PR_CONTENT, id=content.id),
        )
        return TaskDetail(
            task=summary,
            steps=_steps(row),
            people=_pr_people(content, approvals, names, reviewers.get(content.id, ())),
            fields=self._pr_fields(detail, latest_link),
            submissions=_pr_submissions(content.code, submissions, versions, names),
            timeline=_pr_timeline(content, transitions, approvals, submissions, names),
            actions=await self._pr_actions(actor, content),
        )

    def _pr_fields(self, detail: ContentDetail, latest_link: str | None) -> tuple[TaskField, ...]:
        content = detail.content
        version = detail.current_version
        zone = self._settings.timezone
        planned = (
            ensure_utc(content.planned_publish_at).astimezone(zone).date().isoformat()
            if content.planned_publish_at is not None
            else None
        )
        channels = ", ".join(sorted(channel.name for channel in detail.target_channels)) or None
        return (
            TaskField("brief", "Mô tả", content.brief, "longtext", "common"),
            TaskField(
                "content",
                "Nội dung",
                version.script_text if version is not None else None,
                "longtext",
                "common",
            ),
            TaskField("product_link", "Link sản phẩm", latest_link, "link", "common"),
            TaskField(
                "brand", "Thương hiệu", detail.brand.name if detail.brand else None, "text", "pr"
            ),
            TaskField(
                "content_type",
                "Loại nội dung",
                PR_CONTENT_TYPE_LABELS.get(content.content_type) if content.content_type else None,
                "text",
                "pr",
            ),
            TaskField("topic", "Chủ đề", content.topic, "text", "pr"),
            TaskField("hook", "Hook", content.hook, "longtext", "pr"),
            TaskField("channels", "Kênh dự kiến", channels, "text", "pr"),
            TaskField(
                "priority", "Ưu tiên PR", PR_PRIORITY_LABELS.get(content.priority), "text", "pr"
            ),
            TaskField("planned_publish_at", "Lịch đăng", planned, "date", "pr"),
        )

    async def _pr_actions(self, actor: Actor, content: PrContentItem) -> tuple[TaskActionView, ...]:
        offered = await self._pr.actions.for_content(actor=actor, content=content)
        views: list[TaskActionView] = []
        for action in offered:
            if action.kind not in PR_WORKFLOW_KINDS:
                continue
            view = await self._pr_action(action, content)
            if view is not None:
                views.append(view)
        return tuple(views)

    async def _pr_action(
        self, action: PrAvailableAction, content: PrContentItem
    ) -> TaskActionView | None:
        emphasis: Emphasis = action.emphasis.value
        kind = action.kind
        if kind is PrActionKind.TRANSITION and action.target_stage is not None:
            target = action.target_stage
            return TaskActionView(
                key=f"pr:TRANSITION:{target.value}",
                label=PR_TRANSITION_LABELS.get(
                    target, f"Chuyển sang {PR_STAGE_LABELS.get(target, target.value)}"
                ),
                emphasis=emphasis,
                requires_note=False,
                inputs=("note",),
            )
        if kind is PrActionKind.APPROVAL and action.decision is not None:
            decision = action.decision
            label = PR_DECISION_LABELS[decision]
            if (
                content.workflow_stage is PrWorkflowStage.INTERNAL_REVIEW
                and decision is PrApprovalDecision.APPROVED
            ):
                label = "Duyệt nội bộ"
            return TaskActionView(
                key=f"pr:APPROVAL:{decision.value}",
                label=label,
                emphasis=emphasis,
                requires_note=False,
                inputs=("note",),
            )
        if kind is PrActionKind.SET_PRIORITY:
            prioritised = content.priority is not PrPriority.NORMAL
            return TaskActionView(
                key="pr:SET_PRIORITY",
                label="Bỏ ưu tiên" if prioritised else "Đánh dấu ưu tiên",
                emphasis="SECONDARY",
                requires_note=False,
            )
        if kind is PrActionKind.ASSIGN_PRODUCER:
            return TaskActionView(
                key="pr:ASSIGN_PRODUCER",
                label="Giao người sản xuất",
                emphasis=emphasis,
                requires_note=False,
                inputs=("assignee",),
                assignee_options=await self._pr_producers(),
            )
        if kind is PrActionKind.CLAIM_PRODUCTION:
            return TaskActionView(
                key="pr:CLAIM_PRODUCTION",
                label="Nhận sản xuất",
                emphasis=emphasis,
                requires_note=False,
            )
        if kind is PrActionKind.START_PRODUCTION:
            return TaskActionView(
                key="pr:START_PRODUCTION",
                label="Bắt đầu sản xuất",
                emphasis=emphasis,
                requires_note=False,
            )
        if kind is PrActionKind.SUBMIT_PRODUCTION:
            return TaskActionView(
                key="pr:SUBMIT_PRODUCTION",
                label="Gửi duyệt nội bộ",
                emphasis=emphasis,
                requires_note=False,
                inputs=("link", "note"),
            )
        if kind is PrActionKind.UNDO_LAST_ACTION:
            return TaskActionView(
                key="pr:UNDO_LAST_ACTION",
                label=PR_UNDO_LABELS.get(action.undo_kind or "", "Hoàn tác"),
                emphasis=emphasis,
                requires_note=False,
            )
        return None

    async def _pr_producers(self) -> tuple[PersonRef, ...]:
        """Who may hold a production: ``PR_PRODUCTION_EXECUTE``, asked of the
        same capability service the assignment checks."""
        capabilities: PrCapabilityService = self._pr.capabilities
        users = (
            await self._session.scalars(
                select(User).where(User.active.is_(True)).order_by(User.full_name, User.id)
            )
        ).all()
        options: list[PersonRef] = []
        for user in users:
            candidate = Actor(
                user_id=user.id, full_name=user.full_name, role=user.role or Role.EMPLOYEE
            )
            if await capabilities.allows(candidate, PrCapability.PR_PRODUCTION_EXECUTE):
                options.append(PersonRef(user.id, user.full_name))
        return tuple(options)


# --- pure shaping ----------------------------------------------------------------


def unit_of(task: Task) -> UnitCode:
    return UnitCode.PR if task.source_type == SOURCE_PR_CONTENT else UnitCode.ADS


def _steps(row: TaskRow) -> tuple[TaskStep, ...]:
    return tuple(
        TaskStep(
            key=cell.key,
            label=cell.label,
            person_name=cell.person_name,
            status=cell.status,
            status_label=cell.status_label,
            is_current=cell.is_current,
            since=cell.since,
            revisions=cell.revisions,
        )
        for cell in row.cells
    )


def _current_person(row: TaskRow) -> PersonRef | None:
    """The row's holder: a named member, "Chờ giao" (no id), or nobody."""
    if row.current_person_name is None:
        return None
    return PersonRef(row.current_person_user_id, row.current_person_name)


def _ads_people(detail: OrderDetail, approvers: AdsApprovers) -> tuple[TaskPerson, ...]:
    """Every step names people, never a role: who did it, or who it waits for."""
    order = detail.order
    names = detail.names
    people = [TaskPerson("Người order", order.owner_user_id, names.get(order.owner_user_id, ""))]

    def waiting(label: str, group: People) -> TaskPerson:
        return TaskPerson(
            label, group[0].user_id if len(group) == 1 else None, format_names(group) or "Chờ giao"
        )

    if order.order_approved_by_user_id is not None:
        people.append(
            TaskPerson(
                "Duyệt order",
                order.order_approved_by_user_id,
                names.get(order.order_approved_by_user_id, ""),
            )
        )
    elif order.stage is OrderStage.ORDER_PENDING:
        people.append(waiting("Chờ duyệt order", approvers.head))
    for node in detail.nodes:
        # A legacy link node is no step of its own any more.
        if node.status is OrderNodeStatus.BO_QUA or node.node_type is OrderNodeType.GAN_LINK:
            continue
        person = node.assignee_user_id or node.preassigned_user_id
        if person is not None:
            people.append(
                TaskPerson(node_type_label(node.node_type), person, names.get(person, ""))
            )
            continue
        team = approvers.assigners_of(node.node_type, order.video_type)
        people.append(
            TaskPerson(
                f"{node_type_label(node.node_type)} · chưa giao, người phân công",
                team[0].user_id if len(team) == 1 else None,
                format_names(team) or "Chờ giao",
            )
        )
    if order.stage is OrderStage.DUYET_VIDEO_BT:
        people.append(waiting("Chờ duyệt video", approvers.video))
    if order.stage is OrderStage.FINAL_REVIEW:
        # The orderer's review; a stand-in may decide but is not named.
        people.append(
            TaskPerson("Chờ duyệt final", order.owner_user_id, names.get(order.owner_user_id, ""))
        )
    return tuple(people)


def _ads_fields(order: Order) -> tuple[TaskField, ...]:
    source = None
    if order.script_source is not None:
        source = "AI" if order.script_source.value == "AI" else "Quay thực tế"
    fields = [
        TaskField("content", "Nội dung", order.order_content, "longtext", "common"),
        TaskField("reference_link", "Link tham khảo", order.reference_link, "link", "common"),
        TaskField("product_link", "Link sản phẩm", order.product_link, "link", "common"),
        TaskField("process", "Quy trình", video_type_label(order.video_type), "text", "ads"),
        TaskField("video_kind", "Loại video", order.video_kind_name, "text", "ads"),
        TaskField(
            "video_kind_points",
            "Điểm hiệu suất",
            None if order.video_kind_points is None else format_points(order.video_kind_points),
            "text",
            "ads",
        ),
        TaskField("script_source", "Nguồn kịch bản", source, "text", "ads"),
        TaskField("design_link", "Link thiết kế", order.design_link, "link", "ads"),
        TaskField("source_link", "Link nguồn", order.source_link, "link", "ads"),
    ]
    if order.returned_reason:
        fields.append(
            TaskField("returned_reason", "Lý do trả", order.returned_reason, "longtext", "ads")
        )
    if order.cancelled_reason:
        fields.append(
            TaskField("cancelled_reason", "Lý do huỷ", order.cancelled_reason, "longtext", "ads")
        )
    return tuple(fields)


def _ads_submissions(detail: OrderDetail) -> tuple[TaskSubmission, ...]:
    order = detail.order
    node_types = {node.id: node.node_type for node in detail.nodes}
    verdicts: dict[uuid.UUID, OrderApprovalDecision] = {}
    for approval in detail.approvals:
        if approval.submission_id is not None:
            verdicts[approval.submission_id] = approval.decision
    for event in detail.events:
        if event.submission_id is None:
            continue
        if event.kind is OrderEventKind.NODE_APPROVED:
            verdicts.setdefault(event.submission_id, OrderApprovalDecision.APPROVED)
        elif event.kind is OrderEventKind.NODE_RETURNED:
            verdicts.setdefault(event.submission_id, OrderApprovalDecision.RETURNED)
    rows = [
        TaskSubmission(
            id=item.id,
            label=f"{order.code}_V{item.submission_no}",
            step_label=node_type_label(node_types[item.node_id]),
            person_name=detail.names.get(item.submitted_by_user_id),
            link=item.link,
            text=item.script_text,
            note=item.note,
            submitted_at=ensure_utc(item.created_at),
            status_label=(
                None
                if item.id not in verdicts
                else (
                    "Đã duyệt" if verdicts[item.id] is OrderApprovalDecision.APPROVED else "Trả sửa"
                )
            ),
        )
        for item in detail.submissions
    ]
    return tuple(sorted(rows, key=lambda row: row.submitted_at, reverse=True))


def _ads_timeline(detail: OrderDetail) -> tuple[TaskTimelineEntry, ...]:
    node_types = {node.id: node.node_type for node in detail.nodes}
    entries: list[TaskTimelineEntry] = []
    for event in detail.events:
        label = event_label(event.kind)
        if event.node_id is not None and event.node_id in node_types:
            label = f"{label} · {node_type_label(node_types[event.node_id])}"
        if event.kind is OrderEventKind.NODE_ASSIGNED and event.assignee_user_id is not None:
            label = f"{label} → {detail.names.get(event.assignee_user_id, '')}"
        entries.append(
            TaskTimelineEntry(
                at=ensure_utc(event.created_at),
                actor_name=detail.names.get(event.actor_user_id),
                label=label,
                note=event.note,
            )
        )
    return tuple(reversed(entries))


def _pr_people(
    content: PrContentItem,
    approvals: list[PrApprovalEvent],
    names: dict[uuid.UUID, str],
    reviewers: People = (),
) -> tuple[TaskPerson, ...]:
    people = [
        TaskPerson(
            "Người tạo", content.created_by_user_id, names.get(content.created_by_user_id, "")
        ),
        TaskPerson("Phụ trách", content.owner_user_id, names.get(content.owner_user_id, "")),
    ]
    if content.producer_user_id is not None:
        people.append(
            TaskPerson(
                "Sản xuất", content.producer_user_id, names.get(content.producer_user_id, "")
            )
        )
    else:
        people.append(TaskPerson("Sản xuất", None, "Chưa có người nhận"))
    latest: dict[PrApprovalStage, uuid.UUID] = {}
    for approval in approvals:
        latest[approval.approval_stage] = approval.reviewer_user_id
    for stage, user_id in latest.items():
        people.append(TaskPerson(PR_GATE_LABELS[stage], user_id, names.get(user_id, "")))
    if content.workflow_stage in STAGE_APPROVAL_GATES:
        # The gate it stands at now: everybody whose grant reaches it, by name.
        people.append(
            TaskPerson(
                f"Chờ {PR_GATE_LABELS[STAGE_APPROVAL_GATES[content.workflow_stage]].lower()}",
                reviewers[0].user_id if len(reviewers) == 1 else None,
                format_names(reviewers) or "Chờ giao",
            )
        )
    return tuple(people)


def _artifact_link(item: PrProductionSubmission) -> tuple[str | None, str | None]:
    if item.artifact_type is PrProductionArtifactType.NAS_PATH:
        return None, item.location
    return item.location, None


def _pr_submissions(
    code: str,
    submissions: list[PrProductionSubmission],
    versions: list[PrContentVersion],
    names: dict[uuid.UUID, str],
) -> tuple[TaskSubmission, ...]:
    rows: list[TaskSubmission] = []
    for item in submissions:
        link, text = _artifact_link(item)
        rows.append(
            TaskSubmission(
                id=item.id,
                label=item.label or f"{code}_V{item.submission_no}",
                step_label="Sản xuất",
                person_name=names.get(item.submitted_by_user_id),
                link=link,
                text=text,
                note=item.note,
                submitted_at=ensure_utc(item.created_at),
                status_label=None,
            )
        )
    for version in versions:
        rows.append(
            TaskSubmission(
                id=version.id,
                label=f"Kịch bản V{version.version_no}",
                step_label="Kịch bản",
                person_name=names.get(version.created_by_user_id),
                link=None,
                text=version.script_text,
                note=version.change_note,
                submitted_at=ensure_utc(version.created_at),
                status_label=None,
            )
        )
    return tuple(sorted(rows, key=lambda row: row.submitted_at, reverse=True))


def _transition_label(from_stage: PrWorkflowStage, to_stage: PrWorkflowStage, trigger: str) -> str:
    to_label = PR_STAGE_LABELS.get(to_stage, to_stage.value)
    if trigger == PrTransitionTrigger.UNDO.value:
        return f"Hoàn tác về {to_label}"
    if trigger == PrTransitionTrigger.MANUAL.value and from_stage is PrWorkflowStage.SCRIPTING:
        if to_stage is PrWorkflowStage.AI_REVIEW:
            return "Gửi AI review"
        if to_stage is PrWorkflowStage.TEAM_LEAD_REVIEW:
            return "Bỏ qua AI review, gửi duyệt Trưởng nhóm"
    if trigger == PrTransitionTrigger.AI_REVIEW.value:
        if to_stage is PrWorkflowStage.TEAM_LEAD_REVIEW:
            return "AI review xong, gửi duyệt Trưởng nhóm"
        if to_stage is PrWorkflowStage.SCRIPTING:
            return "AI review yêu cầu chỉnh sửa"
    return f"→ {to_label}"


def _pr_timeline(
    content: PrContentItem,
    transitions: list[PrContentTransitionEvent],
    approvals: list[PrApprovalEvent],
    submissions: list[PrProductionSubmission],
    names: dict[uuid.UUID, str],
) -> tuple[TaskTimelineEntry, ...]:
    entries = [
        TaskTimelineEntry(
            at=ensure_utc(content.created_at),
            actor_name=names.get(content.created_by_user_id),
            label="Tạo nội dung",
            note=None,
        )
    ]
    for event in transitions:
        if event.approval_event_id is not None or event.production_submission_id is not None:
            continue  # told by the decision or the hand-in below
        trigger = getattr(event.trigger, "value", str(event.trigger))
        entries.append(
            TaskTimelineEntry(
                at=ensure_utc(event.created_at),
                actor_name=names.get(event.actor_user_id) if event.actor_user_id else None,
                label=_transition_label(event.from_stage, event.to_stage, trigger),
                note=event.note,
            )
        )
    for approval in approvals:
        entries.append(
            TaskTimelineEntry(
                at=ensure_utc(approval.decided_at),
                actor_name=names.get(approval.reviewer_user_id),
                label=f"{PR_GATE_LABELS[approval.approval_stage]}: "
                f"{PR_DECISION_LABELS[approval.decision]}",
                note=approval.comment,
            )
        )
    for item in submissions:
        entries.append(
            TaskTimelineEntry(
                at=ensure_utc(item.created_at),
                actor_name=names.get(item.submitted_by_user_id),
                label=f"Nộp sản phẩm V{item.submission_no}",
                note=item.note,
            )
        )
    return tuple(sorted(entries, key=lambda entry: entry.at, reverse=True))


__all__ = [
    "ADS_NODE_KINDS",
    "PR_WORKFLOW_KINDS",
    "PersonRef",
    "TaskActionView",
    "TaskDetail",
    "TaskDetailService",
    "TaskField",
    "TaskNotFoundError",
    "TaskPerson",
    "TaskStep",
    "TaskSubmission",
    "TaskSummary",
    "TaskTimelineEntry",
    "not_found",
    "unit_of",
]
