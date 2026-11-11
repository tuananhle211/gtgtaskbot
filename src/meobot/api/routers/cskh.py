"""Hộp chat Chăm sóc khách hàng (0102): ``/api/cskh``.

Page inbox (``CSKH INBOX``): ``/state``, ``/conversations`` (+ ``/{id}``,
``/messages``, ``/callback``, ``/unread``, ``/pancake-tags``, ``/assign``,
``/scan-qr``, ``/phone-check``, ``/pos-orders``, ``/confirm``, ``/zalo``).

Zalo (``CSKH ZALO``): ``/zalo/threads`` (+ ``/{id}``, ``/page``,
``/page-send``, ``/link``, ``/unlink``), ``/zalo/search``, ``/zalo/accounts``
(+ assign, logout), ``/zalo/qr``. The bridge pushes to ``/zalo/bridge`` with
the ``x-bridge-secret`` header (no session; no secret configured = 404).

Connections (``CSKH MANAGE``): ``/connections``, ``/pancake-accounts``,
``/pancake/pages``, ``/pos-keys``, ``/pages`` (connect, update, disconnect),
``/labels``, ``/quick-replies``, ``/settings``.
"""

from __future__ import annotations

import hmac
import json
import logging
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.api.deps import (
    CurrentActorDep,
    RequestIdDep,
    SettingsDep,
    UnitMembershipDep,
    get_session,
)
from meobot.application.audit_service import AuditService
from meobot.application.cskh.access import cskh_access
from meobot.application.cskh.common import CskhClients, load_settings
from meobot.application.cskh.config import CskhConfig
from meobot.application.cskh.inbox import Actor, CskhInbox, inbox_sync
from meobot.application.cskh.zalo import CskhZalo, take_push
from meobot.core.secrets import build_secret_box
from meobot.domain.cskh.errors import CskhForbiddenError
from meobot.domain.cskh.models import MemberRole
from meobot.integrations.pancake.client import HttpPancakeClient, HttpPosClient
from meobot.integrations.zalo_bridge.client import HttpZaloBridge, ZaloBridge

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cskh", tags=["cskh"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
MAX_PUSH_BYTES = 5_000_000

TaskSender = Callable[[str, dict[str, Any]], None]


def get_cskh_clients(app_settings: SettingsDep) -> CskhClients:
    """Pancake / POS / the secret box (fakes in tests)."""
    return CskhClients(
        pancake=HttpPancakeClient(),
        pos=HttpPosClient(),
        secret_box=lambda: build_secret_box(app_settings),
    )


def get_zalo_bridge(app_settings: SettingsDep) -> ZaloBridge:
    secret = app_settings.zalo_bridge_secret
    return HttpZaloBridge(
        app_settings.zalo_bridge_url, secret.get_secret_value() if secret is not None else None
    )


def get_cskh_task_sender() -> TaskSender:
    """Queue a Celery task by name (tests record instead)."""

    def send(name: str, kwargs: dict[str, Any]) -> None:
        from meobot.tasks.celery_app import celery_app

        celery_app.send_task(name, kwargs=kwargs)

    return send


def get_qr_owners(request: Request) -> dict[str, uuid.UUID | None]:
    """QR login session -> who the scanned nick goes to (this API process; 1 worker)."""
    owners: dict[str, uuid.UUID | None] | None = getattr(request.app.state, "cskh_qr_owners", None)
    if owners is None:
        owners = {}
        request.app.state.cskh_qr_owners = owners
    return owners


ClientsDep = Annotated[CskhClients, Depends(get_cskh_clients)]
BridgeDep = Annotated[ZaloBridge, Depends(get_zalo_bridge)]
SenderDep = Annotated[TaskSender, Depends(get_cskh_task_sender)]
OwnersDep = Annotated[dict[str, uuid.UUID | None], Depends(get_qr_owners)]


def _actor(actor: Any, request_id: uuid.UUID) -> Actor:
    return Actor(user_id=actor.user_id, name=actor.full_name or "", request_id=request_id)


def _inbox(session: AsyncSession, clients: CskhClients) -> CskhInbox:
    return CskhInbox(session, clients, AuditService(session))


def _zalo(session: AsyncSession, clients: CskhClients, bridge: ZaloBridge) -> CskhZalo:
    return CskhZalo(session, clients, bridge, AuditService(session))


def _config(session: AsyncSession, clients: CskhClients) -> CskhConfig:
    return CskhConfig(session, clients, AuditService(session))


# --- schemas ----------------------------------------------------------------------------------


class SendRequest(BaseModel):
    text: str = Field(max_length=2000)
    clear_callback: bool = False


class FlagRequest(BaseModel):
    on: bool


class TagRequest(BaseModel):
    tag_id: str = Field(max_length=60)
    add: bool = True


class AssignRequest(BaseModel):
    user_id: str | None = Field(default=None, max_length=80)


class ConfirmRequest(BaseModel):
    confirm_type: str = Field(default="phone", pattern="^(phone|qr)$")
    phone: str = Field(default="", max_length=30)
    customer_name: str = Field(default="", max_length=300)
    address: str = Field(default="", max_length=500)
    content_name: str = Field(default="", max_length=300)
    order_status: str = Field(default="", max_length=120)
    platform: str = Field(default="Facebook", max_length=20)
    marketer_user_id: uuid.UUID | None = None
    creator_user_id: uuid.UUID | None = None
    telesale_user_id: uuid.UUID | None = None
    telesale_name: str = Field(default="", max_length=200)
    customer_code: str = Field(default="", max_length=40)
    note: str = Field(default="", max_length=2000)
    order_id: str | None = Field(default=None, max_length=40)
    dup_ack: bool = False


class LinkRequest(BaseModel):
    term: str = Field(default="", max_length=200)
    conversation_id: uuid.UUID | None = None
    match: str = Field(default="", max_length=200)


class AssignAccountsRequest(BaseModel):
    account_ids: list[str] = Field(max_length=200)
    user_id: uuid.UUID | None = None


class QrStartRequest(BaseModel):
    user_id: uuid.UUID | None = None


class SecretRequest(BaseModel):
    label: str | None = Field(default=None, max_length=120)
    token: str | None = Field(default=None, max_length=2000)
    key: str | None = Field(default=None, max_length=2000)
    expires_on: str | None = Field(default=None, max_length=40)
    sort_order: int | None = None


class MemberItem(BaseModel):
    user_id: uuid.UUID
    role: MemberRole = MemberRole.STAFF


class ConnectRequest(BaseModel):
    #: None = every activated page on the Pancake accounts (not skipped).
    page_ids: list[str] | None = Field(default=None, max_length=500)
    label_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    members: list[MemberItem] = Field(default_factory=list, max_length=50)
    import_history: bool = True


class PagesUpdateRequest(BaseModel):
    page_ids: list[str] = Field(min_length=1, max_length=500)
    active: bool | None = None
    marketer_user_id: uuid.UUID | None = None
    members: list[MemberItem] | None = None
    member_mode: str = Field(default="set", pattern="^(set|add|remove)$")
    label_ids: list[uuid.UUID] | None = None
    label_mode: str = Field(default="set", pattern="^(set|add|remove)$")


class LabelRequest(BaseModel):
    name: str = Field(max_length=80)
    color: str = Field(default="blue", max_length=12)


class QuickReplyRequest(BaseModel):
    shortcut: str = Field(max_length=60)
    text: str = Field(max_length=4000)
    page_id: str | None = Field(default=None, max_length=40)


class ImportRequest(BaseModel):
    text: str = Field(max_length=200_000)
    page_id: str | None = Field(default=None, max_length=40)


class ShareRequest(BaseModel):
    ids: list[uuid.UUID] | None = None
    overwrite: bool = False


# --- the access ---------------------------------------------------------------------------------


@router.get("/access")
async def access_info(membership: UnitMembershipDep) -> dict[str, Any]:
    access = cskh_access(membership)
    return {
        "inbox": access.inbox,
        "zalo": access.zalo,
        "manage": access.manage,
        "sees": access.sees,
    }


# --- the page inbox -----------------------------------------------------------------------


@router.get("/state")
async def inbox_state(
    session: SessionDep, membership: UnitMembershipDep, clients: ClientsDep
) -> dict[str, Any]:
    return await _inbox(session, clients).state(cskh_access(membership))


@router.get("/conversations")
async def list_conversations(
    session: SessionDep,
    membership: UnitMembershipDep,
    clients: ClientsDep,
    page_id: Annotated[str | None, Query(max_length=40)] = None,
    label_id: uuid.UUID | None = None,
    pancake_tag: Annotated[str | None, Query(max_length=60)] = None,
    campaign_id: Annotated[str | None, Query(max_length=60)] = None,
    tab: Annotated[str, Query(pattern="^(all|unread|callback|phone)$")] = "all",
    q: Annotated[str | None, Query(max_length=200)] = None,
    before: datetime | None = None,
    limit: Annotated[int, Query(ge=10, le=200)] = 60,
) -> dict[str, Any]:
    return await _inbox(session, clients).conversations(
        cskh_access(membership),
        page_id=page_id,
        label_id=label_id,
        pancake_tag=pancake_tag,
        campaign_id=campaign_id,
        tab=tab,
        q=q,
        before=before,
        limit=limit,
    )


@router.get("/conversations/{conversation_id}")
async def conversation_detail(
    conversation_id: uuid.UUID,
    session: SessionDep,
    membership: UnitMembershipDep,
    clients: ClientsDep,
    read: bool = True,
) -> dict[str, Any]:
    return await _inbox(session, clients).detail(
        cskh_access(membership), conversation_id, read=read
    )


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: uuid.UUID,
    body: SendRequest,
    session: SessionDep,
    membership: UnitMembershipDep,
    clients: ClientsDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    return await _inbox(session, clients).send(
        cskh_access(membership),
        conversation_id,
        body.text,
        _actor(actor, request_id),
        clear_callback=body.clear_callback,
    )


@router.post("/conversations/{conversation_id}/callback", status_code=204)
async def set_callback(
    conversation_id: uuid.UUID,
    body: FlagRequest,
    session: SessionDep,
    membership: UnitMembershipDep,
    clients: ClientsDep,
) -> Response:
    await _inbox(session, clients).set_callback(cskh_access(membership), conversation_id, body.on)
    return Response(status_code=204)


@router.post("/conversations/{conversation_id}/unread", status_code=204)
async def mark_unread(
    conversation_id: uuid.UUID,
    session: SessionDep,
    membership: UnitMembershipDep,
    clients: ClientsDep,
) -> Response:
    await _inbox(session, clients).mark_unread(cskh_access(membership), conversation_id)
    return Response(status_code=204)


@router.post("/conversations/{conversation_id}/pancake-tags")
async def pancake_tag(
    conversation_id: uuid.UUID,
    body: TagRequest,
    session: SessionDep,
    membership: UnitMembershipDep,
    clients: ClientsDep,
) -> dict[str, Any]:
    tags = await _inbox(session, clients).pancake_tag(
        cskh_access(membership), conversation_id, body.tag_id, body.add
    )
    return {"pancake_tags": tags}


@router.post("/conversations/{conversation_id}/assign")
async def assign_conversation(
    conversation_id: uuid.UUID,
    body: AssignRequest,
    session: SessionDep,
    membership: UnitMembershipDep,
    clients: ClientsDep,
) -> dict[str, Any]:
    ids = await _inbox(session, clients).assign(
        cskh_access(membership), conversation_id, body.user_id
    )
    return {"assignee_ids": ids}


@router.post("/conversations/{conversation_id}/scan-qr")
async def scan_qr(
    conversation_id: uuid.UUID,
    session: SessionDep,
    membership: UnitMembershipDep,
    clients: ClientsDep,
) -> dict[str, Any]:
    inbox = _inbox(session, clients)
    return await inbox.rescan_qr(
        cskh_access(membership), conversation_id, inbox_sync(session, clients)
    )


@router.get("/conversations/{conversation_id}/phone-check")
async def phone_check(
    conversation_id: uuid.UUID,
    session: SessionDep,
    membership: UnitMembershipDep,
    clients: ClientsDep,
    phone: Annotated[str, Query(max_length=30)] = "",
) -> dict[str, Any]:
    return await _inbox(session, clients).phone_check(
        cskh_access(membership), conversation_id, phone.strip()
    )


@router.get("/conversations/{conversation_id}/pos-orders")
async def pos_orders(
    conversation_id: uuid.UUID,
    session: SessionDep,
    membership: UnitMembershipDep,
    clients: ClientsDep,
) -> dict[str, Any]:
    inbox = _inbox(session, clients)
    conv = await inbox.conversation(cskh_access(membership), conversation_id)
    return {"orders": await inbox.pos_orders(conv)}


@router.post("/conversations/{conversation_id}/confirm")
async def confirm(
    conversation_id: uuid.UUID,
    body: ConfirmRequest,
    session: SessionDep,
    membership: UnitMembershipDep,
    clients: ClientsDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    return await _inbox(session, clients).confirm(
        cskh_access(membership),
        conversation_id,
        body.model_dump(mode="json"),
        _actor(actor, request_id),
    )


@router.get("/conversations/{conversation_id}/zalo")
async def conversation_zalo(
    conversation_id: uuid.UUID,
    session: SessionDep,
    membership: UnitMembershipDep,
    clients: ClientsDep,
    bridge: BridgeDep,
) -> dict[str, Any]:
    access = cskh_access(membership)
    access.require_inbox()
    return await _zalo(session, clients, bridge).for_conversation(access, conversation_id)


# --- Zalo ---------------------------------------------------------------------------------------


@router.post("/zalo/bridge")
async def bridge_push(
    request: Request,
    session: SessionDep,
    app_settings: SettingsDep,
    x_bridge_secret: Annotated[str | None, Header()] = None,
) -> Response:
    """The Zalo bridge's push (no session: the shared secret header)."""
    secret = app_settings.zalo_bridge_secret
    expected = secret.get_secret_value() if secret is not None else ""
    if not expected:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    if not x_bridge_secret or not hmac.compare_digest(x_bridge_secret, expected):
        return Response(status_code=status.HTTP_403_FORBIDDEN)
    raw = await request.body()
    if len(raw) > MAX_PUSH_BYTES:
        return Response(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
    try:
        payload = json.loads(raw or b"{}")
    except ValueError:
        return Response(status_code=status.HTTP_400_BAD_REQUEST)
    saved = await take_push(session, payload)
    await session.commit()
    return Response(content=json.dumps({"ok": True, "saved": saved}), media_type="application/json")


@router.get("/zalo/threads")
async def zalo_threads(
    session: SessionDep,
    membership: UnitMembershipDep,
    clients: ClientsDep,
    bridge: BridgeDep,
    account_id: Annotated[str | None, Query(max_length=40)] = None,
    user_id: uuid.UUID | None = None,
    tab: Annotated[str, Query(pattern="^(all|unread|unlinked)$")] = "all",
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> dict[str, Any]:
    threads = await _zalo(session, clients, bridge).threads(
        cskh_access(membership), account_id=account_id, user_id=user_id, tab=tab, q=q
    )
    return {"threads": threads}


@router.get("/zalo/threads/{thread_id}")
async def zalo_thread(
    thread_id: uuid.UUID,
    session: SessionDep,
    membership: UnitMembershipDep,
    clients: ClientsDep,
    bridge: BridgeDep,
    read: bool = True,
) -> dict[str, Any]:
    return await _zalo(session, clients, bridge).messages(
        cskh_access(membership), thread_id, read=read
    )


@router.get("/zalo/threads/{thread_id}/page")
async def zalo_thread_page(
    thread_id: uuid.UUID,
    session: SessionDep,
    membership: UnitMembershipDep,
    clients: ClientsDep,
    bridge: BridgeDep,
) -> dict[str, Any]:
    return await _zalo(session, clients, bridge).page_messages(cskh_access(membership), thread_id)


@router.post("/zalo/threads/{thread_id}/page-send")
async def zalo_page_send(
    thread_id: uuid.UUID,
    body: SendRequest,
    session: SessionDep,
    membership: UnitMembershipDep,
    clients: ClientsDep,
    bridge: BridgeDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    return await _zalo(session, clients, bridge).page_send(
        cskh_access(membership), thread_id, body.text, _actor(actor, request_id)
    )


@router.post("/zalo/threads/{thread_id}/link")
async def zalo_link(
    thread_id: uuid.UUID,
    body: LinkRequest,
    session: SessionDep,
    membership: UnitMembershipDep,
    clients: ClientsDep,
    bridge: BridgeDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    service = _zalo(session, clients, bridge)
    access = cskh_access(membership)
    who = _actor(actor, request_id)
    if body.conversation_id is not None:
        return await service.link_to_conversation(
            access, thread_id, body.conversation_id, who, term=body.term, match=body.match
        )
    return await service.link_by_term(access, thread_id, body.term, who)


@router.post("/zalo/threads/{thread_id}/unlink")
async def zalo_unlink(
    thread_id: uuid.UUID,
    session: SessionDep,
    membership: UnitMembershipDep,
    clients: ClientsDep,
    bridge: BridgeDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    return await _zalo(session, clients, bridge).unlink(
        cskh_access(membership), thread_id, _actor(actor, request_id)
    )


@router.get("/zalo/search")
async def zalo_search(
    session: SessionDep,
    membership: UnitMembershipDep,
    clients: ClientsDep,
    bridge: BridgeDep,
    q: Annotated[str, Query(max_length=200)] = "",
    conversation_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    results = await _zalo(session, clients, bridge).search(
        cskh_access(membership), q, conversation_id
    )
    return {"results": results}


@router.get("/zalo/accounts")
async def zalo_accounts(
    session: SessionDep, membership: UnitMembershipDep, clients: ClientsDep, bridge: BridgeDep
) -> dict[str, Any]:
    return await _zalo(session, clients, bridge).accounts(cskh_access(membership))


@router.post("/zalo/accounts/assign")
async def zalo_assign(
    body: AssignAccountsRequest,
    session: SessionDep,
    membership: UnitMembershipDep,
    clients: ClientsDep,
    bridge: BridgeDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    changed = await _zalo(session, clients, bridge).assign(
        cskh_access(membership), body.account_ids, body.user_id, _actor(actor, request_id)
    )
    return {"changed": changed}


@router.post("/zalo/accounts/{account_id}/logout", status_code=204)
async def zalo_logout(
    account_id: str,
    session: SessionDep,
    membership: UnitMembershipDep,
    clients: ClientsDep,
    bridge: BridgeDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> Response:
    await _zalo(session, clients, bridge).logout(
        cskh_access(membership), account_id[:40], _actor(actor, request_id)
    )
    return Response(status_code=204)


@router.post("/zalo/qr")
async def zalo_qr_start(
    body: QrStartRequest,
    session: SessionDep,
    membership: UnitMembershipDep,
    clients: ClientsDep,
    bridge: BridgeDep,
    owners: OwnersDep,
) -> dict[str, Any]:
    return await _zalo(session, clients, bridge).qr_start(
        cskh_access(membership), body.user_id, owners
    )


@router.get("/zalo/qr/{session_id}")
async def zalo_qr_status(
    session_id: str,
    session: SessionDep,
    membership: UnitMembershipDep,
    clients: ClientsDep,
    bridge: BridgeDep,
    owners: OwnersDep,
) -> dict[str, Any]:
    return await _zalo(session, clients, bridge).qr_status(
        cskh_access(membership), session_id[:80], owners
    )


# --- connections (MANAGE) -----------------------------------------------------------------------


@router.get("/connections")
async def connections(
    session: SessionDep, membership: UnitMembershipDep, clients: ClientsDep
) -> dict[str, Any]:
    return await _config(session, clients).overview(cskh_access(membership))


@router.post("/pancake-accounts", status_code=201)
async def add_pancake_account(
    body: SecretRequest,
    session: SessionDep,
    membership: UnitMembershipDep,
    clients: ClientsDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    return await _config(session, clients).add_pancake_account(
        cskh_access(membership), body.model_dump(exclude_unset=True), _actor(actor, request_id)
    )


@router.patch("/pancake-accounts/{account_id}", status_code=204)
async def update_pancake_account(
    account_id: uuid.UUID,
    body: SecretRequest,
    session: SessionDep,
    membership: UnitMembershipDep,
    clients: ClientsDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> Response:
    await _config(session, clients).update_pancake_account(
        cskh_access(membership),
        account_id,
        body.model_dump(exclude_unset=True),
        _actor(actor, request_id),
    )
    return Response(status_code=204)


@router.delete("/pancake-accounts/{account_id}", status_code=204)
async def remove_pancake_account(
    account_id: uuid.UUID,
    session: SessionDep,
    membership: UnitMembershipDep,
    clients: ClientsDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> Response:
    await _config(session, clients).remove_pancake_account(
        cskh_access(membership), account_id, _actor(actor, request_id)
    )
    return Response(status_code=204)


@router.get("/pancake/pages")
async def pancake_listing(
    session: SessionDep, membership: UnitMembershipDep, clients: ClientsDep
) -> dict[str, Any]:
    return {"pages": await _config(session, clients).pancake_listing(cskh_access(membership))}


@router.post("/pos-keys", status_code=201)
async def add_pos_key(
    body: SecretRequest,
    session: SessionDep,
    membership: UnitMembershipDep,
    clients: ClientsDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    return await _config(session, clients).add_pos_key(
        cskh_access(membership), body.model_dump(exclude_unset=True), _actor(actor, request_id)
    )


@router.delete("/pos-keys/{key_id}", status_code=204)
async def remove_pos_key(
    key_id: uuid.UUID,
    session: SessionDep,
    membership: UnitMembershipDep,
    clients: ClientsDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> Response:
    await _config(session, clients).remove_pos_key(
        cskh_access(membership), key_id, _actor(actor, request_id)
    )
    return Response(status_code=204)


@router.post("/pos-keys/reload")
async def reload_pos(
    session: SessionDep, membership: UnitMembershipDep, clients: ClientsDep
) -> dict[str, Any]:
    return {"shops": await _config(session, clients).reload_pos(cskh_access(membership))}


@router.post("/pages/connect", status_code=202)
async def connect_pages(
    body: ConnectRequest,
    session: SessionDep,
    membership: UnitMembershipDep,
    clients: ClientsDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    sender: SenderDep,
) -> dict[str, Any]:
    ids = await _config(session, clients).begin_connect(
        cskh_access(membership), body.page_ids, _actor(actor, request_id)
    )
    await session.commit()
    sender(
        "cskh.connect_pages",
        {
            "page_ids": ids,
            "label_ids": [str(x) for x in body.label_ids],
            "members": [{"user_id": str(m.user_id), "role": m.role.value} for m in body.members],
            "import_history": body.import_history,
        },
    )
    return {"started": True, "total": len(ids)}


@router.post("/pages/refresh", status_code=202)
async def refresh_pages(membership: UnitMembershipDep, sender: SenderDep) -> dict[str, Any]:
    cskh_access(membership).require_manage()
    sender("cskh.refresh_meta", {})
    return {"started": True}


@router.post("/pages/update")
async def update_pages(
    body: PagesUpdateRequest,
    session: SessionDep,
    membership: UnitMembershipDep,
    clients: ClientsDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    data = body.model_dump(mode="json", exclude_unset=True)
    data.pop("page_ids", None)
    changed = await _config(session, clients).update_pages(
        cskh_access(membership), body.page_ids, data, _actor(actor, request_id)
    )
    return {"changed": changed}


@router.post("/pages/{page_id}/disconnect", status_code=204)
async def disconnect_page(
    page_id: str,
    session: SessionDep,
    membership: UnitMembershipDep,
    clients: ClientsDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> Response:
    await _config(session, clients).disconnect(
        cskh_access(membership), page_id[:40], _actor(actor, request_id)
    )
    return Response(status_code=204)


@router.get("/labels")
async def list_labels(
    session: SessionDep, membership: UnitMembershipDep, clients: ClientsDep
) -> dict[str, Any]:
    access = cskh_access(membership)
    if not access.sees:
        raise CskhForbiddenError()
    return {"labels": await _config(session, clients).labels()}


@router.post("/labels", status_code=201)
async def add_label(
    body: LabelRequest, session: SessionDep, membership: UnitMembershipDep, clients: ClientsDep
) -> dict[str, Any]:
    return await _config(session, clients).save_label(
        cskh_access(membership), None, body.model_dump()
    )


@router.patch("/labels/{label_id}")
async def edit_label(
    label_id: uuid.UUID,
    body: LabelRequest,
    session: SessionDep,
    membership: UnitMembershipDep,
    clients: ClientsDep,
) -> dict[str, Any]:
    return await _config(session, clients).save_label(
        cskh_access(membership), label_id, body.model_dump()
    )


@router.delete("/labels/{label_id}", status_code=204)
async def delete_label(
    label_id: uuid.UUID, session: SessionDep, membership: UnitMembershipDep, clients: ClientsDep
) -> Response:
    await _config(session, clients).delete_label(cskh_access(membership), label_id)
    return Response(status_code=204)


@router.get("/quick-replies")
async def list_quick_replies(
    session: SessionDep, membership: UnitMembershipDep, clients: ClientsDep
) -> dict[str, Any]:
    return {"quick_replies": await _config(session, clients).quick_replies(cskh_access(membership))}


@router.post("/quick-replies", status_code=201)
async def add_quick_reply(
    body: QuickReplyRequest, session: SessionDep, membership: UnitMembershipDep, clients: ClientsDep
) -> dict[str, Any]:
    return await _config(session, clients).save_quick_reply(
        cskh_access(membership), None, body.model_dump()
    )


@router.patch("/quick-replies/{reply_id}")
async def edit_quick_reply(
    reply_id: uuid.UUID,
    body: QuickReplyRequest,
    session: SessionDep,
    membership: UnitMembershipDep,
    clients: ClientsDep,
) -> dict[str, Any]:
    return await _config(session, clients).save_quick_reply(
        cskh_access(membership), reply_id, body.model_dump()
    )


@router.delete("/quick-replies/{reply_id}", status_code=204)
async def delete_quick_reply(
    reply_id: uuid.UUID, session: SessionDep, membership: UnitMembershipDep, clients: ClientsDep
) -> Response:
    await _config(session, clients).delete_quick_reply(cskh_access(membership), reply_id)
    return Response(status_code=204)


@router.post("/quick-replies/import")
async def import_quick_replies(
    body: ImportRequest, session: SessionDep, membership: UnitMembershipDep, clients: ClientsDep
) -> dict[str, Any]:
    return await _config(session, clients).import_quick_replies(
        cskh_access(membership), body.text, body.page_id
    )


@router.post("/quick-replies/share")
async def share_quick_replies(
    body: ShareRequest, session: SessionDep, membership: UnitMembershipDep, clients: ClientsDep
) -> dict[str, Any]:
    return await _config(session, clients).share_quick_replies(
        cskh_access(membership), body.ids, body.overwrite
    )


@router.get("/settings")
async def get_settings(session: SessionDep, membership: UnitMembershipDep) -> dict[str, Any]:
    cskh_access(membership).require_manage()
    return (await load_settings(session)).as_dict()


@router.put("/settings")
async def put_settings(
    request: Request,
    session: SessionDep,
    membership: UnitMembershipDep,
    clients: ClientsDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    try:
        data = await request.json()
    except ValueError:
        data = {}
    return await _config(session, clients).save_settings(
        cskh_access(membership), data if isinstance(data, dict) else {}, _actor(actor, request_id)
    )


__all__ = ["get_cskh_clients", "get_cskh_task_sender", "get_qr_owners", "get_zalo_bridge", "router"]
