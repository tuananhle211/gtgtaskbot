"""Chăm sóc khách hàng (0094): Pancake pages answered with a Hermes agent.

* ``POST /api/cskh/pancake/webhook/{key}`` - Pancake's events. No session: the
  key in the address is the secret (unknown = 404). Answers 200 at once; the
  agent is asked by a task in this process (``CskhRunner``).
* ``GET /api/cskh/access`` - what the caller may do here.
* ``/api/cskh/replies`` - the suggestions: list by tab, the live thread, send
  (as is or edited), dismiss; ``/conversations/{id}/resume`` lifts a pause.
  ``CSKH REPLY`` (OWNER / ADMIN by role).
* ``/api/cskh/pages`` - the pages: create / edit / delete, "Kiểm tra token".
  ``CSKH MANAGE`` (the OWNER by role).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.api.deps import (
    RequestIdDep,
    SettingsDep,
    UnitMembershipDep,
    get_llm_provider,
    get_session,
)
from meobot.api.routers.agents import RelayDep, TransactionsDep
from meobot.application.audit_service import AuditService
from meobot.application.cskh.access import cskh_access
from meobot.application.cskh.pages import CskhPageService, webhook_url
from meobot.application.cskh.pipeline import (
    CskhDeps,
    CskhInbox,
    CskhRunner,
    ReplyRow,
    paused_label,
    script_reviewer,
    take_webhook,
)
from meobot.core.config import Settings
from meobot.core.secrets import SecretBox, build_secret_box
from meobot.db.models.pancake import PancakeConversation, PancakePage, PancakeReply
from meobot.db.models.user import User
from meobot.domain.pancake.models import (
    REASON_LABELS,
    REPLY_MODE_LABELS,
    REPLY_STATUS_LABELS,
    ReplyMode,
)
from meobot.integrations.pancake.client import HttpPancakeClient, PancakeClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cskh", tags=["cskh"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
#: Pancake's bodies are small; anything bigger is not an event.
MAX_WEBHOOK_BYTES = 1_000_000


def get_pancake_client() -> PancakeClient:
    """Pancake's Page API (a fake in tests)."""
    return HttpPancakeClient()


def get_cskh_runner(request: Request) -> CskhRunner:
    runner: CskhRunner | None = getattr(request.app.state, "cskh_runner", None)
    if runner is None:
        runner = CskhRunner()
        request.app.state.cskh_runner = runner
    return runner


def get_cskh_secret_box(app_settings: SettingsDep) -> Any:
    """A callable giving the deployment's secret box (a test box in tests)."""

    def box() -> SecretBox:
        return build_secret_box(app_settings)

    return box


def get_cskh_debounce() -> float | None:
    """None = the domain default (tests set 0)."""
    return None


PancakeDep = Annotated[PancakeClient, Depends(get_pancake_client)]
RunnerDep = Annotated[CskhRunner, Depends(get_cskh_runner)]
BoxDep = Annotated[Any, Depends(get_cskh_secret_box)]
DebounceDep = Annotated[float | None, Depends(get_cskh_debounce)]


# --- schemas ------------------------------------------------------------------------------


class CskhAccessResponse(BaseModel):
    reply: bool
    manage: bool
    configured: bool


class PageResponse(BaseModel):
    id: uuid.UUID
    page_id: str
    name: str
    active: bool
    mode: str
    mode_label: str
    agent_slug: str
    access_token_hint: str
    has_token: bool
    webhook_url: str
    handoff_tag_ids: list[str]
    stop_when_assigned: bool
    last_event_at: datetime | None
    last_checked_at: datetime | None
    last_check_ok: bool | None
    last_check_message: str | None

    @classmethod
    def of(cls, page: PancakePage, settings: Settings) -> PageResponse:
        return cls(
            id=page.id,
            page_id=page.page_id,
            name=page.name,
            active=page.active,
            mode=page.mode.value,
            mode_label=REPLY_MODE_LABELS[page.mode],
            agent_slug=page.agent_slug,
            access_token_hint=page.access_token_hint,
            has_token=bool(page.access_token_encrypted),
            webhook_url=webhook_url(settings, page.webhook_key),
            handoff_tag_ids=[str(t) for t in page.handoff_tag_ids or []],
            stop_when_assigned=page.stop_when_assigned,
            last_event_at=page.last_event_at,
            last_checked_at=page.last_checked_at,
            last_check_ok=page.last_check_ok,
            last_check_message=page.last_check_message,
        )


class AgentChoice(BaseModel):
    slug: str
    name: str


class PageListResponse(BaseModel):
    pages: list[PageResponse]
    agents: list[AgentChoice]
    modes: list[dict[str, str]]
    secret_ready: bool
    configured: bool


class PageRequest(BaseModel):
    page_id: str | None = Field(default=None, max_length=200)
    name: str | None = Field(default=None, max_length=300)
    mode: str | None = None
    agent_slug: str | None = Field(default=None, max_length=60)
    access_token: str | None = Field(default=None, max_length=2000)
    handoff_tag_ids: list[str] | None = None
    stop_when_assigned: bool | None = None
    active: bool | None = None
    rotate_webhook_key: bool | None = None


class ReplyResponse(BaseModel):
    id: uuid.UUID
    page_row_id: uuid.UUID
    page_name: str
    conversation_row_id: uuid.UUID
    status: str
    status_label: str
    reason: str | None
    reason_label: str | None
    suggested_text: str | None
    sent_text: str | None
    handoff: bool
    handoff_reason: str | None
    violations: list[dict[str, Any]]
    review_passed: bool | None
    review_note: str | None
    customer_at: datetime | None
    answered_at: datetime | None
    sent_at: datetime | None
    decided_by: str | None
    auto_sent: bool
    paused: str | None
    paused_label: str | None
    paused_until: datetime | None

    @classmethod
    def of(cls, row: ReplyRow) -> ReplyResponse:
        reply, page, conv = row.reply, row.page, row.conversation
        paused = paused_label(conv)
        return cls(
            id=reply.id,
            page_row_id=page.id,
            page_name=page.name,
            conversation_row_id=conv.id,
            status=reply.status.value,
            status_label=REPLY_STATUS_LABELS[reply.status],
            reason=reply.reason,
            reason_label=REASON_LABELS.get(reply.reason or "", reply.reason),
            suggested_text=reply.suggested_text,
            sent_text=reply.sent_text,
            handoff=reply.handoff,
            handoff_reason=reply.handoff_reason,
            violations=list(reply.violations or []),
            review_passed=reply.review_passed,
            review_note=reply.review_note,
            customer_at=reply.customer_at,
            answered_at=reply.answered_at,
            sent_at=reply.sent_at,
            decided_by=row.decided_by,
            auto_sent=reply.status.value == "SENT" and reply.decided_by_user_id is None,
            paused=paused,
            paused_label=(
                {"handoff": "Agent đã chuyển cho người"}.get(paused, REASON_LABELS.get(paused))
                if paused
                else None
            ),
            paused_until=conv.paused_until if paused else None,
        )


class ReplyListResponse(BaseModel):
    replies: list[ReplyResponse]
    counts: dict[str, int]


class ThreadMessageResponse(BaseModel):
    id: str
    from_page: bool
    text: str
    at: datetime | None
    attachments: int


class ThreadResponse(BaseModel):
    messages: list[ThreadMessageResponse]


class SendReplyRequest(BaseModel):
    text: str = Field(max_length=5000)


# --- the webhook --------------------------------------------------------------------------


@router.get("/pancake/webhook/{key}")
async def webhook_probe(key: str) -> dict[str, bool]:
    """Some "verify URL" buttons send a GET first: answer it."""
    return {"ok": True}


@router.post("/pancake/webhook/{key}")
async def webhook(
    key: str,
    request: Request,
    session: SessionDep,
    relay: RelayDep,
    transactions: TransactionsDep,
    client: PancakeDep,
    runner: RunnerDep,
    box: BoxDep,
    debounce: DebounceDep,
) -> Response:
    raw = await request.body()
    if len(raw) > MAX_WEBHOOK_BYTES:
        return Response(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
    try:
        payload = json.loads(raw or b"{}")
    except ValueError:
        payload = None
    try:
        conv_id = await take_webhook(session, key[:80], payload)
    except LookupError:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    await session.commit()
    if conv_id is not None:
        deps = CskhDeps(
            relay=relay,
            client=client,
            transactions=transactions,
            secret_box=box,
            reviewer=script_reviewer(get_llm_provider(request)),
        )
        if debounce is not None:
            deps.debounce = debounce
        runner.schedule(conv_id, deps)
    return Response(content=b'{"ok":true}', media_type="application/json")


# --- access ---------------------------------------------------------------------------------


@router.get("/access", response_model=CskhAccessResponse)
async def access_info(membership: UnitMembershipDep, relay: RelayDep) -> CskhAccessResponse:
    access = cskh_access(membership)
    return CskhAccessResponse(reply=access.sees, manage=access.manage, configured=relay.configured)


# --- the inbox ------------------------------------------------------------------------------


@router.get("/replies", response_model=ReplyListResponse)
async def list_replies(
    session: SessionDep,
    membership: UnitMembershipDep,
    box: BoxDep,
    tab: Annotated[str, Query(pattern="^(waiting|sent|other)$")] = "waiting",
    page_row_id: uuid.UUID | None = None,
) -> ReplyListResponse:
    inbox = CskhInbox(session, AuditService(session), box)
    rows, counts = await inbox.rows(cskh_access(membership), tab=tab, page_row_id=page_row_id)
    return ReplyListResponse(replies=[ReplyResponse.of(row) for row in rows], counts=counts)


@router.get("/replies/{reply_id}/thread", response_model=ThreadResponse)
async def reply_thread(
    reply_id: uuid.UUID,
    session: SessionDep,
    membership: UnitMembershipDep,
    box: BoxDep,
    client: PancakeDep,
) -> ThreadResponse:
    inbox = CskhInbox(session, AuditService(session), box)
    messages = await inbox.thread(cskh_access(membership), reply_id, client)
    return ThreadResponse(
        messages=[
            ThreadMessageResponse(
                id=m.id, from_page=m.from_page, text=m.text, at=m.at, attachments=m.attachments
            )
            for m in messages
        ]
    )


async def _one(session: AsyncSession, reply: PancakeReply) -> ReplyResponse:
    page = await session.get(PancakePage, reply.page_row_id)
    conv = await session.get(PancakeConversation, reply.conversation_row_id)
    assert page is not None and conv is not None
    decider = (
        await session.get(User, reply.decided_by_user_id) if reply.decided_by_user_id else None
    )
    return ReplyResponse.of(ReplyRow(reply, page, conv, decider.full_name if decider else None))


@router.post("/replies/{reply_id}/send", response_model=ReplyResponse)
async def send_reply(
    reply_id: uuid.UUID,
    body: SendReplyRequest,
    session: SessionDep,
    membership: UnitMembershipDep,
    request_id: RequestIdDep,
    box: BoxDep,
    client: PancakeDep,
) -> ReplyResponse:
    inbox = CskhInbox(session, AuditService(session), box)
    reply = await inbox.send(
        cskh_access(membership), reply_id, body.text, client, request_id=request_id
    )
    return await _one(session, reply)


@router.post("/replies/{reply_id}/dismiss", response_model=ReplyResponse)
async def dismiss_reply(
    reply_id: uuid.UUID,
    session: SessionDep,
    membership: UnitMembershipDep,
    request_id: RequestIdDep,
    box: BoxDep,
) -> ReplyResponse:
    inbox = CskhInbox(session, AuditService(session), box)
    reply = await inbox.dismiss(cskh_access(membership), reply_id, request_id=request_id)
    return await _one(session, reply)


@router.post("/conversations/{conversation_row_id}/resume", status_code=204)
async def resume_conversation(
    conversation_row_id: uuid.UUID,
    session: SessionDep,
    membership: UnitMembershipDep,
    box: BoxDep,
) -> Response:
    inbox = CskhInbox(session, AuditService(session), box)
    await inbox.resume(cskh_access(membership), conversation_row_id)
    return Response(status_code=204)


# --- the pages ------------------------------------------------------------------------------


def _pages(session: AsyncSession, settings: Settings, box: Any) -> CskhPageService:
    return CskhPageService(session, AuditService(session), settings, secret_box=box)


@router.get("/pages", response_model=PageListResponse)
async def list_pages(
    session: SessionDep,
    membership: UnitMembershipDep,
    app_settings: SettingsDep,
    relay: RelayDep,
    box: BoxDep,
) -> PageListResponse:
    service = _pages(session, app_settings, box)
    pages = await service.pages(cskh_access(membership))
    return PageListResponse(
        pages=[PageResponse.of(page, app_settings) for page in pages],
        agents=[AgentChoice(slug=a.slug, name=a.name) for a in await service.agents()],
        modes=[{"value": m.value, "label": REPLY_MODE_LABELS[m]} for m in ReplyMode],
        secret_ready=service.secret_ready(),
        configured=relay.configured,
    )


@router.post("/pages", response_model=PageResponse, status_code=201)
async def create_page(
    body: PageRequest,
    session: SessionDep,
    membership: UnitMembershipDep,
    app_settings: SettingsDep,
    request_id: RequestIdDep,
    box: BoxDep,
) -> PageResponse:
    page = await _pages(session, app_settings, box).create(
        cskh_access(membership), body.model_dump(exclude_unset=True), request_id=request_id
    )
    return PageResponse.of(page, app_settings)


@router.patch("/pages/{page_row_id}", response_model=PageResponse)
async def update_page(
    page_row_id: uuid.UUID,
    body: PageRequest,
    session: SessionDep,
    membership: UnitMembershipDep,
    app_settings: SettingsDep,
    request_id: RequestIdDep,
    box: BoxDep,
) -> PageResponse:
    page = await _pages(session, app_settings, box).update(
        cskh_access(membership),
        page_row_id,
        body.model_dump(exclude_unset=True),
        request_id=request_id,
    )
    return PageResponse.of(page, app_settings)


@router.delete("/pages/{page_row_id}", status_code=204)
async def delete_page(
    page_row_id: uuid.UUID,
    session: SessionDep,
    membership: UnitMembershipDep,
    app_settings: SettingsDep,
    request_id: RequestIdDep,
    box: BoxDep,
) -> Response:
    await _pages(session, app_settings, box).delete(
        cskh_access(membership), page_row_id, request_id=request_id
    )
    return Response(status_code=204)


@router.post("/pages/{page_row_id}/check", response_model=PageResponse)
async def check_page(
    page_row_id: uuid.UUID,
    session: SessionDep,
    membership: UnitMembershipDep,
    app_settings: SettingsDep,
    box: BoxDep,
    client: PancakeDep,
) -> PageResponse:
    page = await _pages(session, app_settings, box).check(
        cskh_access(membership), page_row_id, client
    )
    return PageResponse.of(page, app_settings)
