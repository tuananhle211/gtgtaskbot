"""Hộp chat Chăm sóc khách hàng (0102). See :mod:`meobot.domain.cskh.models`.

Connections (MANAGE): ``cskh_pancake_accounts`` (user tokens, encrypted, AAD
``cskh_pancake_account:<id>``), ``cskh_pos_keys`` (POS Cake API keys, AAD
``cskh_pos_key:<id>``), ``cskh_pos_shops`` (what the keys see),
``cskh_pages`` (one Facebook page: its Pancake page token, encrypted with AAD
``cskh_page:<page id>``, marketer, POS shop), ``cskh_page_members`` (Chủ page /
Nhân viên trực = who sees it with ``INBOX OWN``), ``cskh_labels`` +
``cskh_page_labels`` (free page labels), ``cskh_pancake_tags`` /
``cskh_pancake_users`` (Pancake's own, mirrored every 10 minutes),
``cskh_quick_replies``.

Data: ``cskh_conversations`` (one customer of one page), ``cskh_messages``
(unique Pancake / Facebook message id), ``cskh_ads`` (the ads that brought
customers), ``cskh_orders`` (phone / QR confirmations sent to POS Cake),
``cskh_zalo_accounts`` / ``cskh_zalo_threads`` / ``cskh_zalo_messages``.

Unlike 0094, customer data IS stored (names, phone numbers, messages): the
search, the duplicate-number check and the Zalo matching need it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from meobot.db.base import Base, JSONColumn, TimestampMixin, UUIDPrimaryKeyMixin

USERS = "users.id"
CASCADE = "CASCADE"
SET_NULL = "SET NULL"
RESTRICT = "RESTRICT"


def _false() -> Any:
    return text("false")


class CskhPancakeAccount(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "cskh_pancake_accounts"

    label: Mapped[str] = mapped_column(String(120), nullable=False)
    token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    token_hint: Mapped[str] = mapped_column(String(12), nullable=False, default="")
    #: Earlier = wins a page both accounts hold (when both activate it).
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Pancake user tokens last 90 days: typed in to be warned before.
    expires_on: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(USERS, ondelete=SET_NULL), nullable=True
    )


class CskhPosKey(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "cskh_pos_keys"

    label: Mapped[str] = mapped_column(String(120), nullable=False)
    key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    key_hint: Mapped[str] = mapped_column(String(12), nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    shop_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(USERS, ondelete=SET_NULL), nullable=True
    )


class CskhPosShop(Base, TimestampMixin):
    """A POS Cake shop (ids are POS's); a shop two keys see is kept once (first key)."""

    __tablename__ = "cskh_pos_shops"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    key_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cskh_pos_keys.id", ondelete=CASCADE), nullable=False
    )
    #: ``[{id, name}]`` the shop's Facebook pages (each Apexmed page has one shop).
    pages: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)


class CskhPage(Base, TimestampMixin):
    """A Facebook page read through Pancake. ``id`` = the page id (Pancake's = Facebook's)."""

    __tablename__ = "cskh_pages"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    username: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    #: The Pancake account that generated the page token.
    pancake_account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cskh_pancake_accounts.id", ondelete=SET_NULL), nullable=True
    )
    token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_hint: Mapped[str] = mapped_column(String(12), nullable=False, default="")
    #: Read every 20 s while on and holding a token ("Ngắt kết nối" = off).
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    marketer_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(USERS, ondelete=SET_NULL), nullable=True
    )
    pos_shop_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    #: None = never read: its first read loads history and counts nothing as new.
    polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    poll_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    meta_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CskhPageMember(Base, TimestampMixin):
    __tablename__ = "cskh_page_members"

    page_id: Mapped[str] = mapped_column(
        ForeignKey("cskh_pages.id", ondelete=CASCADE), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(USERS, ondelete=CASCADE), primary_key=True
    )
    #: ``OWNER`` (Chủ page) / ``STAFF`` (Nhân viên trực).
    role: Mapped[str] = mapped_column(String(10), nullable=False, default="STAFF")


class CskhLabel(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "cskh_labels"

    name: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    color: Mapped[str] = mapped_column(String(12), nullable=False, default="blue")


class CskhPageLabel(Base):
    __tablename__ = "cskh_page_labels"

    page_id: Mapped[str] = mapped_column(
        ForeignKey("cskh_pages.id", ondelete=CASCADE), primary_key=True
    )
    label_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cskh_labels.id", ondelete=CASCADE), primary_key=True
    )


class CskhPancakeTag(Base):
    __tablename__ = "cskh_pancake_tags"

    page_id: Mapped[str] = mapped_column(
        ForeignKey("cskh_pages.id", ondelete=CASCADE), primary_key=True
    )
    tag_id: Mapped[str] = mapped_column(String(60), primary_key=True)
    text: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    color: Mapped[str] = mapped_column(String(20), nullable=False, default="#888780")


class CskhPancakeUser(Base):
    __tablename__ = "cskh_pancake_users"

    page_id: Mapped[str] = mapped_column(
        ForeignKey("cskh_pages.id", ondelete=CASCADE), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, default="")


class CskhQuickReply(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """``/chao`` -> text (``{ten}`` = the customer's name). ``page_id`` None = every page."""

    __tablename__ = "cskh_quick_replies"
    __table_args__ = (
        Index(
            "uq_cskh_quick_replies_shared",
            "shortcut",
            unique=True,
            postgresql_where=text("page_id IS NULL"),
            sqlite_where=text("page_id IS NULL"),
        ),
        Index(
            "uq_cskh_quick_replies_page",
            "shortcut",
            "page_id",
            unique=True,
            postgresql_where=text("page_id IS NOT NULL"),
            sqlite_where=text("page_id IS NOT NULL"),
        ),
    )

    shortcut: Mapped[str] = mapped_column(String(40), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    page_id: Mapped[str | None] = mapped_column(
        ForeignKey("cskh_pages.id", ondelete=CASCADE), nullable=True
    )


class CskhConversation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "cskh_conversations"
    __table_args__ = (
        UniqueConstraint("page_id", "psid"),
        Index("ix_cskh_conversations_last_at", "last_at"),
        Index("ix_cskh_conversations_page_last_at", "page_id", "last_at"),
    )

    page_id: Mapped[str] = mapped_column(
        ForeignKey("cskh_pages.id", ondelete=RESTRICT), nullable=False
    )
    #: The customer's id for this page (Facebook PSID, the same on Pancake).
    psid: Mapped[str] = mapped_column(String(80), nullable=False)
    customer_name: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    customer_key: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    last_text: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    last_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_out: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=_false()
    )
    last_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Pancake's ``updated_at`` last seen: newer = read the thread again.
    pancake_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    unread: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: "Chờ gọi lại" (the out-of-hours answer sets it; an answer clears it).
    callback: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=_false()
    )
    last_auto_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Pancake tag ids / assignee ids last seen (strings).
    pancake_tags: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)
    assignee_ids: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)
    #: A staff edit of tags / assignee: Pancake's list is not taken for a minute.
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    phones: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)
    #: Every number's digits, space separated (search, duplicate check).
    phone_digits: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ad_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    #: ``[{ad_id, post_id, at}]`` newest first.
    ads: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)
    #: ``[{text, kind, mid, at, url}]`` QR codes read from the customer's pictures.
    qrs: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)
    qr_texts: Mapped[str] = mapped_column(Text, nullable=False, default="")


class CskhMessage(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "cskh_messages"
    __table_args__ = (Index("ix_cskh_messages_conversation_at", "conversation_id", "at"),)

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cskh_conversations.id", ondelete=CASCADE), nullable=False
    )
    mid: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    direction: Mapped[str] = mapped_column(String(3), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    text_key: Mapped[str] = mapped_column(Text, nullable=False, default="")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: ``ok`` / ``lỗi: ...`` for what APM sent.
    status: Mapped[str] = mapped_column(String(300), nullable=False, default="ok")
    attachments: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)
    #: QR texts read from its pictures; None = not read yet (only customers' pictures are).
    qr: Mapped[list[Any] | None] = mapped_column(JSONColumn, nullable=True)
    sender_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(USERS, ondelete=SET_NULL), nullable=True
    )
    sender_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CskhAd(Base):
    __tablename__ = "cskh_ads"

    ad_id: Mapped[str] = mapped_column(String(60), primary_key=True)
    page_id: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    image_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    campaign_id: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    campaign_name: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    account_id: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    #: False = Pancake knew nothing (asked once, not again).
    found: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CskhOrder(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A phone / QR confirmation: the POS order confirmed or created, and what was typed."""

    __tablename__ = "cskh_orders"
    __table_args__ = (
        Index("ix_cskh_orders_conversation", "conversation_id", "created_at"),
        Index("ix_cskh_orders_phone_key", "phone_key"),
        Index("ix_cskh_orders_customer_code", "customer_code"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cskh_conversations.id", ondelete=RESTRICT), nullable=False
    )
    page_id: Mapped[str] = mapped_column(String(40), nullable=False)
    #: ``confirm`` (an order Pancake made moved to "Đã xác nhận") / ``create``.
    action: Mapped[str] = mapped_column(String(10), nullable=False)
    confirm_type: Mapped[str] = mapped_column(String(6), nullable=False, default="phone")
    pos_shop_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    pos_order_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    pos_order_no: Mapped[str | None] = mapped_column(String(40), nullable=True)
    value: Mapped[Decimal | None] = mapped_column(Numeric(14, 0), nullable=True)
    phone: Mapped[str] = mapped_column(String(30), nullable=False, default="")
    phone_key: Mapped[str] = mapped_column(String(12), nullable=False, default="")
    customer_name: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    address: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    content_name: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    order_status: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    platform: Mapped[str] = mapped_column(String(20), nullable=False, default="Facebook")
    marketer_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(USERS, ondelete=SET_NULL), nullable=True
    )
    marketer_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    creator_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(USERS, ondelete=SET_NULL), nullable=True
    )
    creator_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    telesale_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(USERS, ondelete=SET_NULL), nullable=True
    )
    telesale_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    #: Mã KH, upper case without spaces (the Zalo matching key).
    customer_code: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: "Trùng số: ..." / "Đổi marketing A -> B" when confirmed anyway.
    dup_info: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(USERS, ondelete=SET_NULL), nullable=True
    )


class CskhZaloAccount(Base, TimestampMixin):
    """A company Zalo nick linked by QR. ``id`` = its Zalo uid."""

    __tablename__ = "cskh_zalo_accounts"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    phone: Mapped[str] = mapped_column(String(30), nullable=False, default="")
    avatar: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: The telesale it is handed to (``ZALO OWN`` sees it).
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(USERS, ondelete=SET_NULL), nullable=True
    )
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="connected")
    status_note: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    status_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CskhZaloThread(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "cskh_zalo_threads"
    __table_args__ = (
        UniqueConstraint("account_id", "peer_uid"),
        Index("ix_cskh_zalo_threads_last_at", "last_at"),
        Index("ix_cskh_zalo_threads_conversation", "conversation_id"),
    )

    account_id: Mapped[str] = mapped_column(
        ForeignKey("cskh_zalo_accounts.id", ondelete=CASCADE), nullable=False
    )
    #: The customer's Zalo uid (``g<id>`` for a group).
    peer_uid: Mapped[str] = mapped_column(String(60), nullable=False)
    is_group: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=_false()
    )
    peer_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    peer_avatar: Mapped[str] = mapped_column(Text, nullable=False, default="")
    last_text: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    last_out: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=_false()
    )
    last_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    unread: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cskh_conversations.id", ondelete=SET_NULL), nullable=True
    )
    customer_code: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    link_how: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    #: ``code`` (masked phones for the telesale) / ``phone``.
    link_kind: Mapped[str | None] = mapped_column(String(6), nullable=True)
    linked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    linked_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(USERS, ondelete=SET_NULL), nullable=True
    )


class CskhZaloMessage(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "cskh_zalo_messages"
    __table_args__ = (Index("ix_cskh_zalo_messages_thread_at", "thread_id", "at"),)

    thread_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cskh_zalo_threads.id", ondelete=CASCADE), nullable=False
    )
    msg_id: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    direction: Mapped[str] = mapped_column(String(3), nullable=False)
    sender_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: Digits only (phone search) and the folded text (word search).
    text_digits: Mapped[str] = mapped_column(Text, nullable=False, default="")
    text_key: Mapped[str] = mapped_column(Text, nullable=False, default="")
    kind: Mapped[str] = mapped_column(String(40), nullable=False, default="text")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = [
    "CskhAd",
    "CskhConversation",
    "CskhLabel",
    "CskhMessage",
    "CskhOrder",
    "CskhPage",
    "CskhPageLabel",
    "CskhPageMember",
    "CskhPancakeAccount",
    "CskhPancakeTag",
    "CskhPancakeUser",
    "CskhPosKey",
    "CskhPosShop",
    "CskhQuickReply",
    "CskhZaloAccount",
    "CskhZaloMessage",
    "CskhZaloThread",
]
