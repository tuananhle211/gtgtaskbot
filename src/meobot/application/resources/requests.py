"""Đề xuất tài nguyên (2026-10-21): the request flow.

``Tạo đề xuất -> Trưởng phòng duyệt -> Duyệt chi -> Mua & nhập kho -> Xác
nhận nhận`` (see :mod:`meobot.domain.resources.requests`). Who acts where:

* anybody creates a request, and cancels their own until it is funded;
* the head step: the head(s) recorded on the request when it was sent
  (``head_approver_ids``: the heads the requester reports to, else every head
  of their departments; none = the step is skipped);
* "Duyệt chi" and "Mua & nhập kho": the Phòng Tài nguyên (whoever may add
  resources, ``ResourceAccess.create``) - there is no finance department yet;
* the requester confirms the hand-over;
* the OWNER / an ADMIN may act at every step.

The purchase step turns each line ``ID | Link`` into one resource held by the
requester, linked to the request (``resources.request_id``).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.org.seats import load_seats
from meobot.application.resources.access import (
    ResourceAccess,
    ResourceForbiddenError,
    ResourceNotFoundError,
    ResourceValidationError,
)
from meobot.application.resources.excel import department_of_seats
from meobot.application.resources.service import ResourceInput, ResourceService
from meobot.core.errors import ConflictError
from meobot.core.time import utcnow
from meobot.db.models.department import Department
from meobot.db.models.resource import (
    Resource,
    ResourceOption,
    ResourceRequest,
    ResourceRequestEvent,
)
from meobot.db.models.user import User
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.models import Actor, Role
from meobot.domain.org.models import DepartmentFunction, Position
from meobot.domain.resources.models import OptionKind, StatusGroup
from meobot.domain.resources.requests import (
    EVENT_LABELS,
    MAX_QUANTITY,
    OPEN_STAGES,
    SITE_LABELS,
    STAGE_LABELS,
    STEPS,
    RequestEventKind,
    RequestSite,
    RequestStage,
    format_request_code,
)

#: The code counter's prefix (``resource_code_counters``): never a department code.
_COUNTER = "REQ:DX"


class RequestConflictError(ConflictError):
    code = "resource_request_moved"

    def __init__(self) -> None:
        super().__init__(
            "Đề xuất đã chuyển sang bước khác. Tải lại trang để xem.",
            details={"reason": "resource_request_moved"},
        )


@dataclass(frozen=True, slots=True)
class RequestInput:
    type_id: uuid.UUID
    quantity: int
    site: RequestSite
    purpose: str
    reason: str
    #: Optional since 0071.
    budget: int | None = None
    needed_by: date | None = None
    requirements: str | None = None
    replaces: str | None = None


@dataclass(frozen=True, slots=True)
class PersonRef:
    user_id: uuid.UUID
    full_name: str


@dataclass(frozen=True, slots=True)
class EventView:
    row: ResourceRequestEvent
    label: str
    actor: PersonRef | None


@dataclass(frozen=True, slots=True)
class StepView:
    key: str
    label: str
    #: ``done`` / ``current`` / ``todo`` / ``skipped`` / ``stopped``.
    state: str
    #: Who did it (done / stopped), or who it waits on (current).
    person_name: str | None = None
    person_user_id: uuid.UUID | None = None
    #: "Đã duyệt", "Chờ Trần A duyệt", "Từ chối"... - the step's badge.
    status_label: str = ""
    #: When it was done (or stopped).
    at: datetime | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class RequestView:
    row: ResourceRequest
    type_name: str
    requester: PersonRef
    approvers: list[PersonRef]
    stage_label: str
    site_label: str
    awaiting_me: bool
    #: What this viewer may do now: ``approve``, ``reject``, ``fund``,
    #: ``deliver``, ``confirm``, ``cancel``.
    actions: list[str]
    delivered: int
    steps: list[StepView] = field(default_factory=list)
    events: list[EventView] = field(default_factory=list)
    resources: list[Resource] = field(default_factory=list)
    #: How many active resources of this type the requester holds now.
    holding: int = 0


@dataclass(frozen=True, slots=True)
class RequestForm:
    types: list[ResourceOption]
    approvers: list[PersonRef]


def parse_lines(text: str) -> list[tuple[int, str, str | None]]:
    """``ID | Link`` per line -> ``(line number, id, link)``; blank lines skipped."""
    found: list[tuple[int, str, str | None]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        external_id, _sep, link = line.partition("|")
        found.append((number, " ".join(external_id.split()), link.strip() or None))
    return found


class ResourceRequestService:
    def __init__(self, service: ResourceService) -> None:
        self._service = service
        self._session: AsyncSession = service.session

    # --- who -------------------------------------------------------------------------

    @staticmethod
    def _boss(actor: Actor) -> bool:
        return actor.role in (Role.OWNER, Role.ADMIN)

    async def _approvers_for(self, user_id: uuid.UUID) -> list[uuid.UUID]:
        """The heads who decide the head step for ``user_id``'s request: the
        heads they report to, else every head of their departments."""
        everyone = await load_seats(self._session)
        mine = everyone.get(user_id, ())
        managers = {
            seat.manager_user_id
            for seat in mine
            if seat.position is Position.MEMBER and seat.manager_user_id not in (None, user_id)
        }
        if managers:
            return sorted(managers, key=str)  # type: ignore[arg-type]
        departments = {seat.department_id for seat in mine if seat.position is Position.MEMBER}
        heads = {
            other
            for other, seats in everyone.items()
            if other != user_id
            and any(
                seat.department_id in departments and seat.position is Position.HEAD
                for seat in seats
            )
        }
        return sorted(heads, key=str)

    def _sees(self, actor: Actor, access: ResourceAccess, row: ResourceRequest) -> bool:
        return (
            self._boss(actor)
            or access.create
            or access.view_all
            or row.requester_user_id == actor.user_id
            or str(actor.user_id) in (row.head_approver_ids or [])
        )

    def _actions(self, actor: Actor, access: ResourceAccess, row: ResourceRequest) -> list[str]:
        boss = self._boss(actor)
        mine = row.requester_user_id == actor.user_id
        head = str(actor.user_id) in (row.head_approver_ids or [])
        found: list[str] = []
        if row.stage is RequestStage.HEAD_REVIEW and (head or boss):
            found += ["approve", "reject"]
        if row.stage is RequestStage.FUNDING and access.create:
            found += ["fund", "reject"]
        if row.stage is RequestStage.PURCHASING and access.create:
            found.append("deliver")
        if row.stage is RequestStage.DELIVERED and (mine or boss):
            found.append("confirm")
        if mine and row.stage in (RequestStage.HEAD_REVIEW, RequestStage.FUNDING):
            found.append("cancel")
        return found

    def _awaiting(self, actor: Actor, access: ResourceAccess, row: ResourceRequest) -> bool:
        if row.stage is RequestStage.HEAD_REVIEW:
            return str(actor.user_id) in (row.head_approver_ids or []) or (
                self._boss(actor) and not row.head_approver_ids
            )
        if row.stage in (RequestStage.FUNDING, RequestStage.PURCHASING):
            return access.create
        if row.stage is RequestStage.DELIVERED:
            return row.requester_user_id == actor.user_id
        return False

    # --- reading ---------------------------------------------------------------------

    async def form(self, actor: Actor) -> RequestForm:
        catalog = await self._service.catalog()
        approvers = await self._approvers_for(actor.user_id) if actor.user_id else []
        return RequestForm(
            types=catalog.of_kind(OptionKind.TYPE),
            approvers=list((await self._people(approvers)).values()),
        )

    async def holding(self, user_id: uuid.UUID, type_id: uuid.UUID) -> int:
        """Active resources (status in the NORMAL group) of this type held by ``user_id``."""
        statement = (
            select(func.count(Resource.id))
            .join(ResourceOption, ResourceOption.id == Resource.status_id)
            .where(
                Resource.holder_user_id == user_id,
                Resource.deleted_at.is_(None),
                Resource.type_id == type_id,
                ResourceOption.status_group == StatusGroup.NORMAL,
            )
        )
        return int(await self._session.scalar(statement) or 0)

    async def list_requests(
        self, actor: Actor, access: ResourceAccess, *, tab: str = "all"
    ) -> list[RequestView]:
        rows = (
            await self._session.scalars(
                select(ResourceRequest).order_by(
                    ResourceRequest.created_at.desc(), ResourceRequest.id.desc()
                )
            )
        ).all()
        visible = [row for row in rows if self._sees(actor, access, row)]
        if tab == "mine":
            visible = [row for row in visible if self._awaiting(actor, access, row)]
        elif tab == "open":
            visible = [row for row in visible if row.stage in OPEN_STAGES]
        return await self._views(actor, access, visible, full=False)

    async def awaiting_count(self, actor: Actor, access: ResourceAccess) -> int:
        rows = (
            await self._session.scalars(
                select(ResourceRequest).where(ResourceRequest.stage.in_(list(OPEN_STAGES)))
            )
        ).all()
        return sum(
            1
            for row in rows
            if self._sees(actor, access, row) and self._awaiting(actor, access, row)
        )

    async def get(self, actor: Actor, access: ResourceAccess, request_id: uuid.UUID) -> RequestView:
        row = await self._session.get(ResourceRequest, request_id)
        if row is None or not self._sees(actor, access, row):
            raise ResourceNotFoundError()
        return (await self._views(actor, access, [row], full=True))[0]

    async def _people(self, ids: list[uuid.UUID]) -> dict[uuid.UUID, PersonRef]:
        wanted = [item for item in dict.fromkeys(ids) if item is not None]
        if not wanted:
            return {}
        users = (await self._session.scalars(select(User).where(User.id.in_(wanted)))).all()
        found = {user.id: PersonRef(user.id, user.full_name) for user in users}
        return {item: found[item] for item in wanted if item in found}

    async def _views(
        self,
        actor: Actor,
        access: ResourceAccess,
        rows: list[ResourceRequest],
        *,
        full: bool,
    ) -> list[RequestView]:
        if not rows:
            return []
        catalog = await self._service.catalog()
        ids = [row.id for row in rows]
        counted = (
            await self._session.execute(
                select(Resource.request_id, func.count(Resource.id))
                .where(Resource.request_id.in_(ids), Resource.deleted_at.is_(None))
                .group_by(Resource.request_id)
            )
        ).all()
        counts: dict[uuid.UUID | None, int] = {key: int(value) for key, value in counted}
        events: list[ResourceRequestEvent] = []
        resources: list[Resource] = []
        if full:
            events = list(
                (
                    await self._session.scalars(
                        select(ResourceRequestEvent)
                        .where(ResourceRequestEvent.request_id.in_(ids))
                        .order_by(ResourceRequestEvent.created_at, ResourceRequestEvent.id)
                    )
                ).all()
            )
            resources = list(
                (
                    await self._session.scalars(
                        select(Resource)
                        .where(Resource.request_id.in_(ids), Resource.deleted_at.is_(None))
                        .order_by(Resource.code)
                    )
                ).all()
            )
        person_ids: list[uuid.UUID] = []
        for row in rows:
            person_ids.append(row.requester_user_id)
            person_ids += [uuid.UUID(item) for item in row.head_approver_ids or []]
        person_ids += [event.actor_user_id for event in events if event.actor_user_id]
        people = await self._people(person_ids)
        views: list[RequestView] = []
        for row in rows:
            type_option = catalog.get(row.type_id)
            view = RequestView(
                row=row,
                type_name=type_option.name if type_option else "",
                requester=people.get(
                    row.requester_user_id, PersonRef(row.requester_user_id, "(đã xoá)")
                ),
                approvers=[
                    people[uuid.UUID(item)]
                    for item in row.head_approver_ids or []
                    if uuid.UUID(item) in people
                ],
                stage_label=STAGE_LABELS[row.stage],
                site_label=SITE_LABELS[row.site],
                awaiting_me=self._awaiting(actor, access, row),
                actions=self._actions(actor, access, row),
                delivered=int(counts.get(row.id, 0)),
            )
            if full:
                mine_events = [event for event in events if event.request_id == row.id]
                view = replace(
                    view,
                    steps=self._steps(row, mine_events, people, view.requester, view.approvers),
                    events=[
                        EventView(
                            row=event,
                            label=EVENT_LABELS[event.kind],
                            actor=people.get(event.actor_user_id) if event.actor_user_id else None,
                        )
                        for event in reversed(mine_events)
                    ],
                    resources=[item for item in resources if item.request_id == row.id],
                    holding=await self.holding(row.requester_user_id, row.type_id),
                )
            views.append(view)
        return views

    @staticmethod
    def _steps(
        row: ResourceRequest,
        events: list[ResourceRequestEvent],
        people: dict[uuid.UUID, PersonRef],
        requester: PersonRef,
        approvers: list[PersonRef],
    ) -> list[StepView]:
        """The five cards of the process strip, like the task page's: who did
        each step and when, who the current one waits on."""
        first: dict[RequestEventKind, ResourceRequestEvent] = {}
        for line in events:
            first.setdefault(line.kind, line)
        stop = first.get(RequestEventKind.REJECTED) or first.get(RequestEventKind.CANCELLED)
        done_by = {
            "CREATED": RequestEventKind.SUBMITTED,
            "HEAD": RequestEventKind.HEAD_APPROVED,
            "FUNDING": RequestEventKind.FUNDED,
            "PURCHASING": RequestEventKind.DELIVERED,
            "DELIVERED": RequestEventKind.CONFIRMED,
        }
        done_word = {
            "CREATED": "Đã gửi",
            "HEAD": "Đã duyệt",
            "FUNDING": "Đã duyệt chi",
            "PURCHASING": "Đã nhập kho & bàn giao",
            "DELIVERED": "Đã nhận",
        }
        current_key = {
            RequestStage.HEAD_REVIEW: "HEAD",
            RequestStage.FUNDING: "FUNDING",
            RequestStage.PURCHASING: "PURCHASING",
            RequestStage.DELIVERED: "DELIVERED",
        }.get(row.stage)
        heads = ", ".join(person.full_name for person in approvers) or "trưởng phòng"
        waiting = {
            "HEAD": (f"Chờ {heads} duyệt", heads, None),
            "FUNDING": ("Chờ Phòng Tài nguyên duyệt chi", "Phòng Tài nguyên", None),
            "PURCHASING": ("Chờ Phòng Tài nguyên mua & nhập kho", "Phòng Tài nguyên", None),
            "DELIVERED": (
                f"Chờ {requester.full_name} xác nhận",
                requester.full_name,
                requester.user_id,
            ),
        }
        steps: list[StepView] = []
        stopped_yet = False
        for key, label in STEPS:
            event = first.get(done_by[key])
            if event is not None:
                actor = people.get(event.actor_user_id) if event.actor_user_id else None
                steps.append(
                    StepView(
                        key=key,
                        label=label,
                        state="done",
                        person_name=actor.full_name if actor else None,
                        person_user_id=actor.user_id if actor else None,
                        status_label=done_word[key],
                        at=event.created_at,
                        note=event.note,
                    )
                )
                continue
            if key == "HEAD" and RequestEventKind.HEAD_SKIPPED in first:
                skipped = first[RequestEventKind.HEAD_SKIPPED]
                steps.append(
                    StepView(
                        key=key,
                        label=label,
                        state="skipped",
                        status_label="Bỏ qua",
                        at=skipped.created_at,
                        note=skipped.note,
                    )
                )
                continue
            if stop is not None and not stopped_yet:
                stopped_yet = True
                actor = people.get(stop.actor_user_id) if stop.actor_user_id else None
                steps.append(
                    StepView(
                        key=key,
                        label=label,
                        state="stopped",
                        person_name=actor.full_name if actor else None,
                        person_user_id=actor.user_id if actor else None,
                        status_label="Từ chối"
                        if stop.kind is RequestEventKind.REJECTED
                        else "Đã huỷ",
                        at=stop.created_at,
                        note=stop.note,
                    )
                )
                continue
            if key == current_key:
                word, name, user_id = waiting[key]
                steps.append(
                    StepView(
                        key=key,
                        label=label,
                        state="current",
                        person_name=name,
                        person_user_id=user_id,
                        status_label=word,
                    )
                )
                continue
            steps.append(StepView(key=key, label=label, state="todo", status_label="Chưa tới"))
        return steps

    # --- writing ---------------------------------------------------------------------

    async def create(
        self, actor: Actor, access: ResourceAccess, data: RequestInput
    ) -> ResourceRequest:
        if actor.user_id is None:
            raise ResourceForbiddenError()
        catalog = await self._service.catalog()
        type_option = catalog.get(data.type_id)
        if type_option is None or type_option.kind is not OptionKind.TYPE or not type_option.active:
            raise ResourceValidationError(
                "Loại tài nguyên không hợp lệ.",
                details={"reason": "resource_option_invalid", "field": "type_id"},
            )
        if not 1 <= data.quantity <= MAX_QUANTITY:
            raise ResourceValidationError(
                f"Số lượng từ 1 đến {MAX_QUANTITY}.",
                details={"reason": "request_quantity_invalid", "field": "quantity"},
            )
        for name, label in (("purpose", "mục đích sử dụng"), ("reason", "lý do đề xuất")):
            if not getattr(data, name).strip():
                raise ResourceValidationError(
                    f"Cần nhập {label}.",
                    details={"reason": f"request_{name}_missing", "field": name},
                )
        if data.budget is not None and data.budget < 0:
            raise ResourceValidationError(
                "Dự toán không hợp lệ.",
                details={"reason": "request_budget_invalid", "field": "budget"},
            )
        if data.needed_by is not None and data.needed_by < utcnow().date():
            raise ResourceValidationError(
                "Ngày cần có đã qua.",
                details={"reason": "request_needed_by_past", "field": "needed_by"},
            )
        approvers = await self._approvers_for(actor.user_id)
        number = await self._service._next_number(_COUNTER)
        row = ResourceRequest(
            code=format_request_code(number),
            requester_user_id=actor.user_id,
            type_id=type_option.id,
            quantity=data.quantity,
            site=data.site,
            purpose=data.purpose.strip(),
            requirements=(data.requirements or "").strip() or None,
            reason=data.reason.strip(),
            replaces=(data.replaces or "").strip() or None,
            budget=data.budget,
            needed_by=data.needed_by,
            stage=RequestStage.HEAD_REVIEW if approvers else RequestStage.FUNDING,
            head_approver_ids=[str(item) for item in approvers],
        )
        self._session.add(row)
        await self._session.flush()
        self._event(row, RequestEventKind.SUBMITTED, actor)
        if not approvers:
            self._event(
                row,
                RequestEventKind.HEAD_SKIPPED,
                None,
                "Người đề xuất không có trưởng phòng phía trên: chuyển thẳng sang duyệt chi.",
            )
        await self._session.flush()
        await self._audit(actor, AuditAction.RESOURCE_REQUEST_CREATED, row, {"code": row.code})
        return row

    async def decide(
        self,
        actor: Actor,
        access: ResourceAccess,
        request_id: uuid.UUID,
        action: str,
        *,
        note: str | None = None,
        lines: str | None = None,
        actual_cost: int | None = None,
        supplier: str | None = None,
    ) -> ResourceRequest:
        row = await self._session.get(ResourceRequest, request_id)
        if row is None or not self._sees(actor, access, row):
            raise ResourceNotFoundError()
        allowed = self._actions(actor, access, row)
        if action not in allowed:
            if (
                action in ("approve", "reject", "fund", "deliver", "confirm", "cancel")
                and row.stage not in OPEN_STAGES
            ):
                raise RequestConflictError()
            raise ResourceForbiddenError("Bạn không xử lý được bước này của đề xuất.")
        cleaned = (note or "").strip() or None
        if action == "approve":
            row.stage = RequestStage.FUNDING
            self._event(row, RequestEventKind.HEAD_APPROVED, actor, cleaned)
        elif action == "reject":
            if cleaned is None:
                raise ResourceValidationError(
                    "Cần ghi lý do từ chối.",
                    details={"reason": "request_note_missing", "field": "note"},
                )
            row.stage = RequestStage.REJECTED
            row.closed_at = utcnow()
            self._event(row, RequestEventKind.REJECTED, actor, cleaned)
        elif action == "fund":
            row.stage = RequestStage.PURCHASING
            self._event(row, RequestEventKind.FUNDED, actor, cleaned)
        elif action == "deliver":
            count = await self._deliver(actor, access, row, lines or "", actual_cost, supplier)
            row.stage = RequestStage.DELIVERED
            summary = f"{count} tài nguyên, tổng {actual_cost or 0:,} đ".replace(",", ".")
            if row.supplier:
                summary += f", nguồn {row.supplier}"
            self._event(
                row,
                RequestEventKind.DELIVERED,
                actor,
                summary + (f". {cleaned}" if cleaned else ""),
            )
        elif action == "confirm":
            row.stage = RequestStage.COMPLETED
            row.closed_at = utcnow()
            self._event(row, RequestEventKind.CONFIRMED, actor, cleaned)
        elif action == "cancel":
            row.stage = RequestStage.CANCELLED
            row.closed_at = utcnow()
            self._event(row, RequestEventKind.CANCELLED, actor, cleaned)
        row.version += 1
        await self._session.flush()
        await self._audit(
            actor,
            AuditAction.RESOURCE_REQUEST_DELIVERED
            if action == "deliver"
            else AuditAction.RESOURCE_REQUEST_DECIDED,
            row,
            {"code": row.code, "action": action, "stage": row.stage.value},
        )
        return row

    async def _deliver(
        self,
        actor: Actor,
        access: ResourceAccess,
        row: ResourceRequest,
        text: str,
        actual_cost: int | None,
        supplier: str | None,
    ) -> int:
        if actual_cost is None or actual_cost < 0:
            raise ResourceValidationError(
                "Cần nhập tổng tiền mua thực tế.",
                details={"reason": "request_cost_missing", "field": "actual_cost"},
            )
        parsed = parse_lines(text)
        problems: list[str] = []
        if len(parsed) != row.quantity:
            problems.append(f"Cần đúng {row.quantity} dòng, đang có {len(parsed)}.")
        seen: dict[str, int] = {}
        for number, external_id, _link in parsed:
            if not external_id:
                problems.append(f"Dòng {number}: thiếu ID.")
                continue
            key = external_id.casefold()
            if key in seen:
                problems.append(f"Dòng {number}: ID {external_id} trùng dòng {seen[key]}.")
            seen[key] = number
        taken = (
            await self._session.scalars(
                select(Resource.external_id).where(
                    Resource.deleted_at.is_(None),
                    Resource.type_id == row.type_id,
                    func.lower(Resource.external_id).in_(list(seen)),
                )
            )
        ).all()
        for external_id in taken:
            problems.append(f"ID {external_id} đã có trong hệ thống ở cùng loại.")
        if problems:
            raise ResourceValidationError(
                "Danh sách chưa nhập kho được: " + " ".join(problems[:10]),
                details={"reason": "request_lines_invalid", "field": "lines", "problems": problems},
            )
        catalog = await self._service.catalog()
        type_option = catalog.get(row.type_id)
        department = await self._department_for(row.requester_user_id)
        origin = catalog.by_code(OptionKind.ORIGIN, "BOUGHT")
        handover = catalog.by_code(OptionKind.HANDOVER, "DONE")
        maker = replace(access, create=True)
        for _number, external_id, link in parsed:
            resource = await self._service.create(
                access=maker,
                actor=actor,
                request_id=uuid.uuid4(),
                data=ResourceInput(
                    department_id=department.id,
                    type_id=row.type_id,
                    external_id=external_id,
                    name=f"{type_option.name if type_option else 'Tài nguyên'} {external_id}",
                    link=link,
                    holder_user_id=row.requester_user_id,
                    origin_id=origin.id if origin else None,
                    handover_id=handover.id if handover else None,
                    purpose=row.purpose,
                    note=f"Mua theo đề xuất {row.code}",
                ),
                audit=False,
            )
            resource.request_id = row.id
        row.actual_cost = actual_cost
        row.supplier = (supplier or "").strip() or None
        await self._session.flush()
        return len(parsed)

    async def _department_for(self, user_id: uuid.UUID) -> Department:
        """The department the bought resources follow: the requester's (ADS
        first, then Dựng, Thiết kế, Biên tập), else the Phòng Tài nguyên."""
        seats = (await load_seats(self._session, [user_id])).get(user_id, ())
        wanted = department_of_seats(seats)
        department = await self._session.get(Department, wanted) if wanted is not None else None
        if department is None:
            department = await self._session.scalar(
                select(Department)
                .where(Department.function == DepartmentFunction.RESOURCES, Department.active)
                .limit(1)
            )
        if department is None:
            raise ResourceValidationError(
                "Không có phòng ban nào để nhận tài nguyên.",
                details={"reason": "request_department_missing"},
            )
        return department

    def _event(
        self,
        row: ResourceRequest,
        kind: RequestEventKind,
        actor: Actor | None,
        note: str | None = None,
    ) -> None:
        self._session.add(
            ResourceRequestEvent(
                request_id=row.id,
                kind=kind,
                actor_user_id=actor.user_id if actor else None,
                note=note,
            )
        )

    async def _audit(
        self, actor: Actor, action: AuditAction, row: ResourceRequest, after: dict[str, Any]
    ) -> None:
        await self._service.record(
            actor, uuid.uuid4(), action, "resource_request", row.id, None, after
        )


__all__ = [
    "RequestConflictError",
    "RequestForm",
    "RequestInput",
    "RequestView",
    "ResourceRequestService",
    "parse_lines",
]
