"""Đề xuất tài nguyên (2026-10-21): ``/api/resources/requests``.

Registered BEFORE the resources router, whose ``/{resource_id}`` would
otherwise take ``requests`` for an id. Every check is in
:class:`~meobot.application.resources.requests.ResourceRequestService`.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field

from meobot.api.deps import (
    CurrentActorDep,
    ResourceAccessDep,
    ResourceServiceDep,
    SettingsDep,
)
from meobot.application.resources.requests import (
    RequestInput,
    RequestView,
    ResourceRequestService,
)
from meobot.core.config import Settings
from meobot.domain.resources.requests import MAX_QUANTITY, SITE_LABELS, RequestSite

router = APIRouter(prefix="/api/resources/requests", tags=["resources"])


class PersonOut(BaseModel):
    user_id: uuid.UUID
    full_name: str


class StepOut(BaseModel):
    key: str
    label: str
    state: str
    person_name: str | None = None
    person_user_id: uuid.UUID | None = None
    status_label: str = ""
    at: datetime | None = None
    note: str | None = None


class EventOut(BaseModel):
    id: uuid.UUID
    kind: str
    label: str
    at: datetime
    actor: PersonOut | None
    note: str | None


class RequestResourceOut(BaseModel):
    id: uuid.UUID
    code: str
    external_id: str
    name: str
    link: str | None
    detail_path: str


class RequestOut(BaseModel):
    id: uuid.UUID
    code: str
    type_id: uuid.UUID
    type_name: str
    quantity: int
    site: str
    site_label: str
    purpose: str
    requirements: str | None
    reason: str
    replaces: str | None
    budget: int | None
    needed_by: date | None
    stage: str
    stage_label: str
    requester: PersonOut
    approvers: list[PersonOut]
    actual_cost: int | None
    supplier: str | None
    created_at: datetime
    awaiting_me: bool
    actions: list[str]
    #: How many resources were put into the inventory for it.
    delivered: int
    detail_path: str
    steps: list[StepOut] = []
    events: list[EventOut] = []
    resources: list[RequestResourceOut] = []
    holding: int = 0

    @classmethod
    def of(cls, view: RequestView) -> RequestOut:
        row = view.row
        return cls(
            id=row.id,
            code=row.code,
            type_id=row.type_id,
            type_name=view.type_name,
            quantity=row.quantity,
            site=row.site.value,
            site_label=view.site_label,
            purpose=row.purpose,
            requirements=row.requirements,
            reason=row.reason,
            replaces=row.replaces,
            budget=row.budget,
            needed_by=row.needed_by,
            stage=row.stage.value,
            stage_label=view.stage_label,
            requester=PersonOut(user_id=view.requester.user_id, full_name=view.requester.full_name),
            approvers=[PersonOut(user_id=p.user_id, full_name=p.full_name) for p in view.approvers],
            actual_cost=row.actual_cost,
            supplier=row.supplier,
            created_at=row.created_at,
            awaiting_me=view.awaiting_me,
            actions=view.actions,
            delivered=view.delivered,
            detail_path=f"/resources/requests/{row.id}",
            steps=[
                StepOut(
                    key=s.key,
                    label=s.label,
                    state=s.state,
                    person_name=s.person_name,
                    person_user_id=s.person_user_id,
                    status_label=s.status_label,
                    at=s.at,
                    note=s.note,
                )
                for s in view.steps
            ],
            events=[
                EventOut(
                    id=e.row.id,
                    kind=e.row.kind.value,
                    label=e.label,
                    at=e.row.created_at,
                    actor=PersonOut(user_id=e.actor.user_id, full_name=e.actor.full_name)
                    if e.actor
                    else None,
                    note=e.row.note,
                )
                for e in view.events
            ],
            resources=[
                RequestResourceOut(
                    id=r.id,
                    code=r.code,
                    external_id=r.external_id,
                    name=r.name,
                    link=r.link,
                    detail_path=f"/resources/{r.id}",
                )
                for r in view.resources
            ],
            holding=view.holding,
        )


class RequestListOut(BaseModel):
    items: list[RequestOut]
    awaiting_count: int


class OptionOut(BaseModel):
    value: str
    label: str


class FormOut(BaseModel):
    types: list[OptionOut]
    sites: list[OptionOut]
    #: The heads who will decide the head step; empty = it is skipped.
    approvers: list[PersonOut]
    max_quantity: int


class HoldingOut(BaseModel):
    count: int


class CreateRequestBody(BaseModel):
    type_id: uuid.UUID
    quantity: int = Field(ge=1, le=MAX_QUANTITY)
    site: RequestSite
    purpose: str = Field(max_length=2000)
    requirements: str | None = Field(default=None, max_length=4000)
    reason: str = Field(max_length=4000)
    replaces: str | None = Field(default=None, max_length=200)
    #: Optional (0071).
    budget: int | None = Field(default=None, ge=0)
    needed_by: date | None = None


class ActionBody(BaseModel):
    action: Literal["approve", "reject", "fund", "deliver", "confirm", "cancel"]
    note: str | None = Field(default=None, max_length=4000)
    #: ``deliver``: one ``ID | Link`` per line.
    lines: str | None = Field(default=None, max_length=200_000)
    actual_cost: int | None = Field(default=None, ge=0)
    supplier: str | None = Field(default=None, max_length=200)


def _service(
    service: ResourceServiceDep, settings: Settings | None = None
) -> ResourceRequestService:
    """The request service. ``settings`` is passed by the write routes so they
    send notifications; the read routes omit it and send nothing."""
    return ResourceRequestService(service, settings)


@router.get("", response_model=RequestListOut)
async def list_requests(
    actor: CurrentActorDep,
    access: ResourceAccessDep,
    service: ResourceServiceDep,
    tab: Literal["all", "mine", "open"] = Query(default="all"),
) -> RequestListOut:
    """The requests this person may see: their own, those they decide as a
    head, every one for the Phòng Tài nguyên / Ban Giám đốc / OWNER / ADMIN.
    ``tab=mine`` = waiting on them, ``open`` = not finished."""
    requests = _service(service)
    views = await requests.list_requests(actor, access, tab=tab)
    return RequestListOut(
        items=[RequestOut.of(view) for view in views],
        awaiting_count=await requests.awaiting_count(actor, access),
    )


@router.get("/form", response_model=FormOut)
async def request_form(actor: CurrentActorDep, service: ResourceServiceDep) -> FormOut:
    form = await _service(service).form(actor)
    return FormOut(
        types=[OptionOut(value=str(t.id), label=t.name) for t in form.types],
        sites=[OptionOut(value=site.value, label=SITE_LABELS[site]) for site in RequestSite],
        approvers=[PersonOut(user_id=p.user_id, full_name=p.full_name) for p in form.approvers],
        max_quantity=MAX_QUANTITY,
    )


@router.get("/holding", response_model=HoldingOut)
async def holding(
    actor: CurrentActorDep, service: ResourceServiceDep, type_id: uuid.UUID
) -> HoldingOut:
    """How many active resources of this type the caller holds (the form's notice)."""
    if actor.user_id is None:
        return HoldingOut(count=0)
    return HoldingOut(count=await _service(service).holding(actor.user_id, type_id))


@router.post("", response_model=RequestOut, status_code=status.HTTP_201_CREATED)
async def create_request(
    body: CreateRequestBody,
    actor: CurrentActorDep,
    access: ResourceAccessDep,
    service: ResourceServiceDep,
    settings: SettingsDep,
) -> RequestOut:
    requests = _service(service, settings)
    row = await requests.create(
        actor,
        access,
        RequestInput(
            type_id=body.type_id,
            quantity=body.quantity,
            site=body.site,
            purpose=body.purpose,
            reason=body.reason,
            budget=body.budget,
            needed_by=body.needed_by,
            requirements=body.requirements,
            replaces=body.replaces,
        ),
    )
    return RequestOut.of(await requests.get(actor, access, row.id))


@router.get("/{request_id}", response_model=RequestOut)
async def get_request(
    request_id: uuid.UUID,
    actor: CurrentActorDep,
    access: ResourceAccessDep,
    service: ResourceServiceDep,
) -> RequestOut:
    return RequestOut.of(await _service(service).get(actor, access, request_id))


@router.post("/{request_id}/actions", response_model=RequestOut)
async def act_on_request(
    request_id: uuid.UUID,
    body: ActionBody,
    actor: CurrentActorDep,
    access: ResourceAccessDep,
    service: ResourceServiceDep,
    settings: SettingsDep,
) -> RequestOut:
    """One step: ``approve`` / ``reject`` (head step, or the spend step),
    ``fund`` (Duyệt chi), ``deliver`` (Mua & nhập kho: ``lines``,
    ``actual_cost``, ``supplier``), ``confirm`` (the requester), ``cancel``."""
    requests = _service(service, settings)
    await requests.decide(
        actor,
        access,
        request_id,
        body.action,
        note=body.note,
        lines=body.lines,
        actual_cost=body.actual_cost,
        supplier=body.supplier,
    )
    return RequestOut.of(await requests.get(actor, access, request_id))
