"""Phòng Ads (0078): Meta Business Managers, their ad accounts, daily spend and charges.

See :mod:`meobot.domain.ads.models`. A BM's System User token is stored
encrypted (:mod:`meobot.core.secrets`, AAD ``ads_connection:<id>``); only its
last four characters stay in clear, for the screen.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
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

from meobot.core.time import utcnow
from meobot.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, value_enum
from meobot.db.models.pr import RESTRICT, USERS_TABLE
from meobot.domain.ads.crm import CrmRegion
from meobot.domain.ads.models import AdsBrand, AdsPlatform, RevenueSource

ADS_CONNECTIONS = "ads_connections"
ADS_ACCOUNTS = "ads_accounts"
ADS_SPEND_DAILY = "ads_spend_daily"
ADS_CHARGES_DAILY = "ads_charges_daily"
ADS_REVENUE_DAILY = "ads_revenue_daily"
ADS_REVENUE_CHANGES = "ads_revenue_changes"
ADS_KPIS = "ads_kpis"
ADS_MANUAL_CHANGES = "ads_manual_changes"
ADS_BUDGETS = "ads_budgets"
ADS_CRM_ACCOUNTS = "ads_crm_accounts"
ADS_CRM_PAYMENTS = "ads_crm_payments"


class AdsConnection(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One Meta Business Manager the app reads, tied to a cơ sở."""

    __tablename__ = ADS_CONNECTIONS
    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="name_not_empty"),
        CheckConstraint("length(trim(business_id)) > 0", name="business_id_not_empty"),
    )

    name: Mapped[str] = mapped_column(String(80), nullable=False)
    business_id: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    #: "Sài Gòn", "Hà Nội"... - the dashboards' "Cơ sở" filter.
    site: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    token_hint: Mapped[str] = mapped_column(String(12), nullable=False, default="")
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    #: The name Meta gives the BM, from the last test / sync.
    meta_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    account_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_test_message: Mapped[str | None] = mapped_column(String(300), nullable=True)
    last_spend_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_spend_sync_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_spend_sync_message: Mapped[str | None] = mapped_column(String(300), nullable=True)
    last_charge_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_charge_sync_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_charge_sync_message: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )


class AdsAccount(Base, TimestampMixin):
    """One ad account ("TK quảng cáo"): a Meta one as last seen in its BM, or
    (0081) a Google / TikTok Ads one a marketer added, whose numbers are typed."""

    __tablename__ = ADS_ACCOUNTS
    __table_args__ = (
        Index("ix_ads_accounts_user", "user_id"),
        CheckConstraint(
            "platform <> 'FACEBOOK' OR connection_id IS NOT NULL",
            name="facebook_has_connection",
        ),
    )

    #: Meta's numeric id, without ``act_``; a typed-in account
    #: ``google:<id>`` / ``tiktok:<id>`` (:func:`manual_account_key`).
    account_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    #: The BM it was read from; NULL for a typed-in account.
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{ADS_CONNECTIONS}.id", ondelete=RESTRICT), nullable=True
    )
    platform: Mapped[AdsPlatform] = mapped_column(
        value_enum(AdsPlatform, name="ads_platform", length=10),
        nullable=False,
        default=AdsPlatform.FACEBOOK,
        server_default=text("'FACEBOOK'"),
    )
    #: A typed-in account's id as the marketer typed it (``account_id`` has
    #: the platform in front).
    external_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    #: A typed-in account's cơ sở (a Meta one's is its BM's).
    site: Mapped[str | None] = mapped_column(String(60), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    #: Meta's ``account_status`` (1 = active, 2 = disabled, 3 = unsettled...).
    status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    #: 0079: the last four digits of the card paying for it (Meta's
    #: ``funding_source_details``), for "ID TKQC · Số thẻ".
    card_last4: Mapped[str | None] = mapped_column(String(8), nullable=True)
    brand: Mapped[AdsBrand] = mapped_column(
        value_enum(AdsBrand, name="ads_brand", length=10), nullable=False
    )
    #: The brand was set by hand: a rename on Meta no longer changes it.
    brand_custom: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    #: The marketer who runs it; NULL = "Chưa gán".
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    assigned_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AdsSpendDaily(Base):
    """One account's day, as Meta reports it (re-pulled at 23:00 for the day before)."""

    __tablename__ = ADS_SPEND_DAILY

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    account_id: Mapped[str] = mapped_column(
        ForeignKey(f"{ADS_ACCOUNTS}.account_id", ondelete=RESTRICT), primary_key=True
    )
    spend: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    messages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    phones: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AdsChargeDaily(Base):
    """Meta's billing charges on one account in one day (Vietnam time), summed."""

    __tablename__ = ADS_CHARGES_DAILY

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    account_id: Mapped[str] = mapped_column(
        ForeignKey(f"{ADS_ACCOUNTS}.account_id", ondelete=RESTRICT), primary_key=True
    )
    #: What Meta charged (= "Số tiền lập hoá đơn", ``invoice_factor`` 1).
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    charges: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AdsRevenueDaily(Base, UUIDPrimaryKeyMixin):
    """Revenue the Trưởng phòng entered for one day (0079): one marketer's, or
    one ◆ source's. No row = not entered (different from 0)."""

    __tablename__ = ADS_REVENUE_DAILY
    __table_args__ = (
        UniqueConstraint("day", "subject_key", name="uq_ads_revenue_daily_day_subject_key"),
    )

    day: Mapped[date] = mapped_column(Date, nullable=False)
    #: ``u:<user id>`` or ``s:<source>`` (:func:`meobot.domain.ads.models.subject_key`).
    subject_key: Mapped[str] = mapped_column(String(60), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    source: Mapped[RevenueSource | None] = mapped_column(
        value_enum(RevenueSource, name="ads_revenue_source", length=12), nullable=True
    )
    #: Customers served at the HCM site ("Doanh số SG"); for a ◆ source, its amount.
    amount: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    #: A marketer's Sài Gòn customers served in Hà Nội ("Doanh số HN"): their
    #: KPI counts it, the HCM site's revenue does not.
    amount_hn: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


class AdsRevenueChange(Base, UUIDPrimaryKeyMixin):
    """Append-only history of every revenue edit: who changed which day's line
    from what to what."""

    __tablename__ = ADS_REVENUE_CHANGES
    __table_args__ = (Index("ix_ads_revenue_changes_day", "day", "subject_key"),)

    day: Mapped[date] = mapped_column(Date, nullable=False)
    subject_key: Mapped[str] = mapped_column(String(60), nullable=False)
    #: ``sg`` (``amount``) or ``hn`` (``amount_hn``).
    part: Mapped[str] = mapped_column(String(4), nullable=False)
    old_amount: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    new_amount: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    changed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


class AdsKpi(Base, UUIDPrimaryKeyMixin):
    """A month's revenue target (0079): the team's (``team``) or a marketer's."""

    __tablename__ = ADS_KPIS
    __table_args__ = (
        UniqueConstraint("month", "subject_key", name="uq_ads_kpis_month_subject_key"),
    )

    #: The first day of the month.
    month: Mapped[date] = mapped_column(Date, nullable=False)
    subject_key: Mapped[str] = mapped_column(String(60), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


class AdsManualChange(Base, UUIDPrimaryKeyMixin):
    """Append-only history of a typed-in account's numbers (0081): who changed
    which day's spend or invoice from what to what."""

    __tablename__ = ADS_MANUAL_CHANGES
    __table_args__ = (Index("ix_ads_manual_changes_account_day", "account_id", "day"),)

    day: Mapped[date] = mapped_column(Date, nullable=False)
    account_id: Mapped[str] = mapped_column(
        ForeignKey(f"{ADS_ACCOUNTS}.account_id", ondelete=RESTRICT), nullable=False
    )
    #: ``spend`` (chi tiêu) or ``invoice`` (lập hoá đơn).
    field: Mapped[str] = mapped_column(String(10), nullable=False)
    old_amount: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    new_amount: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    changed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


class AdsBudget(Base, UUIDPrimaryKeyMixin):
    """A marketer's ad budget for one of the month's four weeks (0082;
    :mod:`meobot.domain.ads.budget`). No row = not set."""

    __tablename__ = ADS_BUDGETS
    __table_args__ = (
        UniqueConstraint("month", "user_id", "week", name="uq_ads_budgets_month_user_id_week"),
        CheckConstraint("week BETWEEN 1 AND 4", name="week_range"),
    )

    #: The first day of the month.
    month: Mapped[date] = mapped_column(Date, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False
    )
    #: 1-4: days 1-7, 8-14, 15-21, the 22nd to the month's end.
    week: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


class AdsCrmAccount(Base):
    """A marketing account on the CRM (0083): whose it is in the app (or which
    ◆ source it fills) and its region - HCM fills "Doanh số SG", HN "Doanh số
    HN". A person holds several. Neither a person nor a source = not counted."""

    __tablename__ = ADS_CRM_ACCOUNTS
    __table_args__ = (CheckConstraint("user_id IS NULL OR source IS NULL", name="one_owner"),)

    #: The CRM's marketing user id.
    crm_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: Listed by the CRM's marketing users (else only seen on a payment).
    in_directory: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    region: Mapped[CrmRegion | None] = mapped_column(
        value_enum(CrmRegion, name="ads_crm_region", length=4), nullable=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    source: Mapped[RevenueSource | None] = mapped_column(
        value_enum(RevenueSource, name="ads_revenue_source", length=12), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


class AdsCrmPayment(Base):
    """One payment read from the CRM (0083): thực thu on the day it was paid.
    Only what the revenue needs - never the customer's name or phone."""

    __tablename__ = ADS_CRM_PAYMENTS
    __table_args__ = (Index("ix_ads_crm_payments_day_marketing", "payment_date", "marketing_id"),)

    #: The CRM's payment id.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    payment_date: Mapped[date] = mapped_column(Date, nullable=False)
    #: Negative for a refund.
    price: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    payment_type: Mapped[int] = mapped_column(Integer, nullable=False)
    #: The CRM marketing account; no FK - a payment may name one not yet listed.
    marketing_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    branch_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    order_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    is_upsale: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


__all__ = [
    "ADS_ACCOUNTS",
    "ADS_BUDGETS",
    "ADS_CHARGES_DAILY",
    "ADS_CONNECTIONS",
    "ADS_CRM_ACCOUNTS",
    "ADS_CRM_PAYMENTS",
    "ADS_KPIS",
    "ADS_MANUAL_CHANGES",
    "ADS_REVENUE_CHANGES",
    "ADS_REVENUE_DAILY",
    "ADS_SPEND_DAILY",
    "AdsAccount",
    "AdsBudget",
    "AdsChargeDaily",
    "AdsConnection",
    "AdsCrmAccount",
    "AdsCrmPayment",
    "AdsKpi",
    "AdsManualChange",
    "AdsRevenueChange",
    "AdsRevenueDaily",
    "AdsSpendDaily",
]
