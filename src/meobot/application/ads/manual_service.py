"""Google Ads and TikTok Ads typed by hand (0081, chốt 28/10).

There is no API for them here yet, so:

* a marketer of the ADS department **adds their own ad account** (platform,
  id, name, cơ sở, the card's last four digits); a Trưởng phòng ADS / the
  OWNER / an ADMIN may add one for anybody seated in ADS;
* in "Khối 3" of the dashboard, the one running it (or a Trưởng phòng / the
  OWNER / an ADMIN) **types each day's spend and "Số tiền lập hoá đơn"**. They
  go into the same daily tables as Meta's numbers (``ads_spend_daily``,
  ``ads_charges_daily``; invoice factor 1), so every dashboard figure is
  computed the same way as Facebook's. A blank cell is "not entered" (the row
  is removed), not 0;
* **every change is kept** in ``ads_manual_changes`` (who, when, which day,
  what was there before), shown in "Lịch sử sửa".

A Facebook account's numbers come from Meta and cannot be typed (422).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.ads.access import AdsAccess
from meobot.application.ads.accounts_service import (
    AdsAccountNotFoundError,
    AdsAssigneeInvalidError,
    ads_people,
)
from meobot.application.ads.sync_service import vn_today
from meobot.application.audit_service import AuditService
from meobot.core.errors import AuthorizationError, ValidationError
from meobot.core.time import utcnow
from meobot.db.models.ads import AdsAccount, AdsChargeDaily, AdsManualChange, AdsSpendDaily
from meobot.db.models.user import User
from meobot.domain.ads.models import (
    MANUAL_PLATFORMS,
    AdsPlatform,
    brand_of,
    clean_external_id,
    manual_account_key,
)
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.identity.models import Actor

#: Ten thousand billion: a typo guard, not a business limit.
MAX_AMOUNT = Decimal("10000000000000")
#: One save carries at most this many cells (a month of two columns for a
#: dozen accounts).
MAX_ENTRIES = 2000
FIELDS = ("spend", "invoice")


class AdsManualForbiddenError(AuthorizationError):
    code = "ads_manual_forbidden"

    def __init__(self) -> None:
        super().__init__(
            "Chỉ người chạy tài khoản (hoặc Trưởng phòng ADS, chủ sở hữu, quản trị viên)"
            " nhập được số của tài khoản này.",
            details={"reason": "ads_manual_forbidden"},
        )


class AdsManualInvalidError(ValidationError):
    code = "ads_manual_invalid"

    def __init__(self, message: str, field: str) -> None:
        super().__init__(message, details={"reason": "ads_manual_invalid", "field": field})


class AdsAccountExistsError(ValidationError):
    code = "ads_account_exists"

    def __init__(self) -> None:
        super().__init__(
            "Tài khoản quảng cáo này đã có trong hệ thống.",
            details={"reason": "ads_account_exists"},
        )


class AdsAccountHasNumbersError(ValidationError):
    code = "ads_account_has_numbers"

    def __init__(self) -> None:
        super().__init__(
            "Tài khoản đã có số nhập: không xoá được (lịch sử phải giữ lại).",
            details={"reason": "ads_account_has_numbers"},
        )


@dataclass(frozen=True, slots=True)
class ManualEntry:
    account_id: str
    day: date
    #: ``spend`` or ``invoice``.
    field: str
    #: ``None`` / "" = clear the cell.
    amount: object


@dataclass(frozen=True, slots=True)
class ChangeView:
    change: AdsManualChange
    account: AdsAccount
    changed_by: str | None


def may_edit(access: AdsAccess, account: AdsAccount) -> bool:
    """Whether the viewer types this account's numbers: a typed-in account,
    theirs - or anybody's for a Trưởng phòng ADS / the OWNER / an ADMIN."""
    if account.platform not in MANUAL_PLATFORMS or not access.view:
        return False
    return access.manage_accounts or (
        account.user_id is not None and account.user_id == access.user_id
    )


def _amount(value: object, field: str) -> Decimal | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        if isinstance(value, int | float | Decimal):
            amount = Decimal(str(value))
        else:
            # Typed text in the Vietnamese format: "1.500.000" (or "1,500,000").
            amount = Decimal(str(value).replace(".", "").replace(",", "").strip())
    except InvalidOperation as error:
        raise AdsManualInvalidError("Số tiền không hợp lệ.", field) from error
    if amount < 0 or amount > MAX_AMOUNT:
        raise AdsManualInvalidError("Số tiền phải từ 0 đến 10.000 tỷ.", field)
    return amount.quantize(Decimal("0.01"))


def _text(value: str | None, limit: int) -> str | None:
    cleaned = " ".join((value or "").split())
    return cleaned[:limit] or None


def _card(value: str | None) -> str | None:
    digits = "".join(char for char in (value or "") if char.isdigit())
    if value and value.strip() and len(digits) != 4:
        raise AdsManualInvalidError("Số thẻ: nhập 4 số cuối.", "card_last4")
    return digits or None


class AdsManualService:
    def __init__(self, session: AsyncSession, audit: AuditService) -> None:
        self._session = session
        self._audit = audit

    async def _editable(self, access: AdsAccess, account_id: str) -> AdsAccount:
        access.require_view()
        account = await self._session.get(AdsAccount, account_id)
        if account is None or not access.sees(account.user_id):
            raise AdsAccountNotFoundError()
        if account.platform not in MANUAL_PLATFORMS:
            raise AdsManualInvalidError(
                "Số Facebook Ads lấy từ Meta, không nhập tay.", "account_id"
            )
        if not may_edit(access, account):
            raise AdsManualForbiddenError()
        return account

    # --- accounts -------------------------------------------------------------

    async def create_account(
        self,
        *,
        actor: Actor,
        access: AdsAccess,
        request_id: uuid.UUID,
        platform: AdsPlatform,
        external_id: str,
        name: str,
        user_id: uuid.UUID | None = None,
        site: str | None = None,
        card_last4: str | None = None,
    ) -> AdsAccount:
        access.require_view()
        if platform not in MANUAL_PLATFORMS:
            raise AdsManualInvalidError(
                "Tài khoản Facebook Ads được đọc từ BM, không thêm tay.", "platform"
            )
        cleaned = clean_external_id(external_id)
        if cleaned is None:
            raise AdsManualInvalidError(
                "ID tài khoản: chữ, số, dấu - _ . (tối đa 30 ký tự).", "external_id"
            )
        title = _text(name, 300)
        if title is None:
            raise AdsManualInvalidError("Nhập tên tài khoản.", "name")
        holder = user_id if access.manage_accounts and user_id else access.user_id
        if holder != access.user_id and not access.manage_accounts:
            raise AdsManualForbiddenError()
        if holder not in {user.id for user in await ads_people(self._session)}:
            raise AdsAssigneeInvalidError()
        key = manual_account_key(platform, cleaned)
        if await self._session.get(AdsAccount, key) is not None:
            raise AdsAccountExistsError()
        now = utcnow()
        account = AdsAccount(
            account_id=key,
            connection_id=None,
            platform=platform,
            external_id=cleaned,
            name=title,
            status=1,
            currency="VND",
            card_last4=_card(card_last4),
            site=_text(site, 60),
            brand=brand_of(title),
            user_id=holder,
            assigned_by_user_id=actor.user_id,
            assigned_at=now,
            created_by_user_id=actor.user_id,
        )
        self._session.add(account)
        await self._session.flush()
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.ADS_ACCOUNT_CREATED.value,
            result=AuditResult.SUCCESS,
            entity_type="ads_account",
            entity_id=key,
            after_data={
                "platform": platform.value,
                "external_id": cleaned,
                "name": title,
                "user_id": str(holder),
            },
        )
        return account

    async def update_account(
        self,
        *,
        actor: Actor,
        access: AdsAccess,
        request_id: uuid.UUID,
        account_id: str,
        name: str | None = None,
        site: str | None = None,
        card_last4: str | None = None,
        fields: Iterable[str] = (),
    ) -> AdsAccount:
        """Only the fields named in ``fields`` change (a blank site / card clears it)."""
        account = await self._editable(access, account_id)
        names = set(fields)
        before: dict[str, object] = {
            "name": account.name,
            "site": account.site,
            "card_last4": account.card_last4,
        }
        if "name" in names:
            title = _text(name, 300)
            if title is None:
                raise AdsManualInvalidError("Nhập tên tài khoản.", "name")
            account.name = title
            if not account.brand_custom:
                account.brand = brand_of(title)
        if "site" in names:
            account.site = _text(site, 60)
        if "card_last4" in names:
            account.card_last4 = _card(card_last4)
        await self._session.flush()
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.ADS_ACCOUNT_UPDATED.value,
            result=AuditResult.SUCCESS,
            entity_type="ads_account",
            entity_id=account.account_id,
            before_data=before,
            after_data={
                "name": account.name,
                "site": account.site,
                "card_last4": account.card_last4,
            },
        )
        return account

    async def delete_account(
        self, *, actor: Actor, access: AdsAccess, request_id: uuid.UUID, account_id: str
    ) -> None:
        """Only an account added by mistake: one with a number, or a history of
        one, stays (422 ``ads_account_has_numbers``)."""
        account = await self._editable(access, account_id)
        used = await self._session.scalar(
            select(
                or_(
                    exists().where(AdsSpendDaily.account_id == account_id),
                    exists().where(AdsChargeDaily.account_id == account_id),
                    exists().where(AdsManualChange.account_id == account_id),
                )
            )
        )
        if used:
            raise AdsAccountHasNumbersError()
        before: dict[str, object] = {
            "platform": account.platform.value,
            "external_id": account.external_id,
            "name": account.name,
            "user_id": str(account.user_id) if account.user_id else None,
        }
        await self._session.delete(account)
        await self._session.flush()
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.ADS_ACCOUNT_DELETED.value,
            result=AuditResult.SUCCESS,
            entity_type="ads_account",
            entity_id=account_id,
            before_data=before,
        )

    # --- the numbers ------------------------------------------------------------

    async def save(
        self,
        *,
        actor: Actor,
        access: AdsAccess,
        request_id: uuid.UUID,
        entries: list[ManualEntry],
    ) -> int:
        """Saves the cells sent; returns how many changed. Every change gets a
        line in ``ads_manual_changes``."""
        access.require_view()
        if len(entries) > MAX_ENTRIES:
            raise AdsManualInvalidError(f"Tối đa {MAX_ENTRIES} ô mỗi lần lưu.", "entries")
        today = vn_today()
        accounts: dict[str, AdsAccount] = {}
        parsed: dict[tuple[str, date, str], Decimal | None] = {}
        for entry in entries:
            if entry.field not in FIELDS:
                raise AdsManualInvalidError("Cột không hợp lệ.", "field")
            if entry.day > today:
                raise AdsManualInvalidError("Không nhập số cho ngày chưa tới.", "day")
            if entry.account_id not in accounts:
                accounts[entry.account_id] = await self._editable(access, entry.account_id)
            parsed[(entry.account_id, entry.day, entry.field)] = _amount(entry.amount, entry.field)
        if not parsed:
            return 0
        ids = list(accounts)
        days = {day for _, day, _ in parsed}
        spend_rows = {
            (row.account_id, row.day): row
            for row in await self._session.scalars(
                select(AdsSpendDaily).where(
                    AdsSpendDaily.account_id.in_(ids), AdsSpendDaily.day.in_(days)
                )
            )
        }
        charge_rows = {
            (row.account_id, row.day): row
            for row in await self._session.scalars(
                select(AdsChargeDaily).where(
                    AdsChargeDaily.account_id.in_(ids), AdsChargeDaily.day.in_(days)
                )
            )
        }
        now = utcnow()
        changed = 0
        for (account_id, day, field), amount in sorted(parsed.items()):
            spend_row = spend_rows.get((account_id, day))
            charge_row = charge_rows.get((account_id, day))
            if field == "spend":
                old = spend_row.spend if spend_row is not None else None
            else:
                old = charge_row.amount if charge_row is not None else None
            if old == amount:
                continue
            if field == "spend":
                if amount is None:
                    await self._session.delete(spend_row)
                    spend_rows.pop((account_id, day))
                elif spend_row is None:
                    spend_rows[(account_id, day)] = AdsSpendDaily(
                        day=day,
                        account_id=account_id,
                        spend=amount,
                        messages=0,
                        phones=0,
                        synced_at=now,
                    )
                    self._session.add(spend_rows[(account_id, day)])
                else:
                    spend_row.spend = amount
                    spend_row.synced_at = now
            elif amount is None:
                await self._session.delete(charge_row)
                charge_rows.pop((account_id, day))
            elif charge_row is None:
                charge_rows[(account_id, day)] = AdsChargeDaily(
                    day=day, account_id=account_id, amount=amount, charges=1, synced_at=now
                )
                self._session.add(charge_rows[(account_id, day)])
            else:
                charge_row.amount = amount
                charge_row.synced_at = now
            self._session.add(
                AdsManualChange(
                    day=day,
                    account_id=account_id,
                    field=field,
                    old_amount=old,
                    new_amount=amount,
                    changed_by_user_id=actor.user_id,
                    changed_at=now,
                )
            )
            changed += 1
        await self._session.flush()
        if changed:
            await self._audit.record_action(
                request_id=request_id,
                actor=actor,
                action=AuditAction.ADS_MANUAL_SAVED.value,
                result=AuditResult.SUCCESS,
                entity_type="ads_manual",
                entity_id=",".join(sorted(ids))[:200],
                after_data={"changed_cells": changed, "accounts": sorted(ids)},
            )
        return changed

    async def changes(
        self,
        access: AdsAccess,
        *,
        start: date,
        end: date,
        account_id: str | None = None,
        user_id: uuid.UUID | None = None,
        platform: AdsPlatform | None = None,
        limit: int = 300,
    ) -> list[ChangeView]:
        """ "Lịch sử sửa": the newest changes first, for the accounts the
        viewer sees (a member: their own)."""
        access.require_view()
        person = user_id if access.view_all else access.user_id
        statement = (
            select(AdsManualChange, AdsAccount)
            .join(AdsAccount, AdsAccount.account_id == AdsManualChange.account_id)
            .where(AdsManualChange.day >= start, AdsManualChange.day <= end)
        )
        if person is not None:
            statement = statement.where(AdsAccount.user_id == person)
        if account_id:
            statement = statement.where(AdsManualChange.account_id == account_id)
        if platform is not None:
            statement = statement.where(AdsAccount.platform == platform)
        rows = list(
            await self._session.execute(
                statement.order_by(
                    AdsManualChange.changed_at.desc(), AdsManualChange.day.desc()
                ).limit(limit)
            )
        )
        people = {change.changed_by_user_id for change, _ in rows if change.changed_by_user_id}
        names = (
            {
                user.id: user.full_name
                for user in await self._session.scalars(select(User).where(User.id.in_(people)))
            }
            if people
            else {}
        )
        return [
            ChangeView(
                change=change,
                account=account,
                changed_by=names.get(change.changed_by_user_id)
                if change.changed_by_user_id
                else None,
            )
            for change, account in rows
        ]

    async def last_entered_at(self, account_ids: list[str]) -> datetime | None:
        if not account_ids:
            return None
        latest: datetime | None = await self._session.scalar(
            select(AdsManualChange.changed_at)
            .where(AdsManualChange.account_id.in_(account_ids))
            .order_by(AdsManualChange.changed_at.desc())
            .limit(1)
        )
        return latest


__all__ = [
    "AdsAccountExistsError",
    "AdsAccountHasNumbersError",
    "AdsManualForbiddenError",
    "AdsManualInvalidError",
    "AdsManualService",
    "ChangeView",
    "ManualEntry",
    "may_edit",
]
