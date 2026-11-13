"""One link CRM marketing account -> APM account for Phòng Ads and Tài chính.

User 13/11: "Thống nhất việc gắn tài khoản crm bên ads với bên tài chính phải
đồng nhất với nhau, những id tài khoản nào gắn bên mkt thì cũng tự động nhảy
sang bên crm bên tài chính".

The links are edited on Quản trị > Tài khoản CRM (`/admin/crm`, moved there
from the Phòng Ads menu 13/11). Both sides key a CRM person by the same CRM
user id: Phòng Ads in
``ads_crm_accounts`` (person / ◆ source / former marketer), Tài chính in
``crm_staff`` (person only). **Phòng Ads is the source for every id it holds**:
``mirror_ads_accounts`` copies its person onto ``crm_staff.user_id`` (creating
the row when the CRM person was never seen by Tài chính) after every Ads edit,
Excel import and CRM sync; Tài chính's "Nhân viên CRM" screen shows those rows
locked (``ads_link``) and ``FinanceSettingsService.update_staff`` refuses them
(422 ``crm_staff_from_ads``). Ids Phòng Ads does not hold (telesales, CSKH,
bác sĩ…) stay Tài chính's to map by hand.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.core.time import utcnow
from meobot.db.models.ads import AdsCrmAccount
from meobot.db.models.finance import CrmStaffRecord
from meobot.domain.ads.models import REVENUE_SOURCE_LABELS

#: The ``crm_staff.roles`` word for a CRM marketing account.
MARKETING_ROLE = "marketing"


@dataclass(frozen=True, slots=True)
class AdsLink:
    """How Phòng Ads holds a CRM id, as Tài chính shows it."""

    #: Phòng Ads gave it a person (copied to ``crm_staff.user_id``).
    person: bool
    #: Not a person on purpose: a ◆ source or a marketer who left - nothing
    #: for Tài chính to map.
    no_person: bool
    label: str


def ads_link(account: AdsCrmAccount) -> AdsLink:
    if account.user_id is not None:
        return AdsLink(True, False, "Gắn bên Phòng Ads")
    if account.former:
        return AdsLink(False, True, "Nhân viên đã nghỉ (Phòng Ads)")
    if account.source is not None:
        label = REVENUE_SOURCE_LABELS.get(account.source, account.source.value)
        return AdsLink(False, True, f"Nguồn ◆ {label} (Phòng Ads)")
    return AdsLink(False, False, "Chưa gắn bên Phòng Ads")


async def ads_links(session: AsyncSession, crm_ids: Iterable[int]) -> dict[int, AdsLink]:
    ids = set(crm_ids)
    if not ids:
        return {}
    return {
        account.crm_id: ads_link(account)
        for account in await session.scalars(
            select(AdsCrmAccount).where(AdsCrmAccount.crm_id.in_(ids))
        )
    }


async def mirror_ads_accounts(session: AsyncSession, crm_ids: Iterable[int] | None = None) -> int:
    """Make ``crm_staff`` say what Phòng Ads says for its accounts (all of
    them, or only ``crm_ids``). Returns how many links changed."""
    statement = select(AdsCrmAccount)
    if crm_ids is not None:
        ids = set(crm_ids)
        if not ids:
            return 0
        statement = statement.where(AdsCrmAccount.crm_id.in_(ids))
    accounts = list((await session.scalars(statement)).all())
    if not accounts:
        return 0
    stored = {
        row.id: row
        for row in await session.scalars(
            select(CrmStaffRecord).where(CrmStaffRecord.id.in_([a.crm_id for a in accounts]))
        )
    }
    now = utcnow()
    changed = 0
    for account in accounts:
        row = stored.get(account.crm_id)
        if row is None:
            row = CrmStaffRecord(id=account.crm_id, name=account.name[:200], roles=[])
            session.add(row)
        if MARKETING_ROLE not in (row.roles or []):
            row.roles = sorted({*(row.roles or []), MARKETING_ROLE})
        if row.user_id != account.user_id:
            row.user_id = account.user_id
            row.updated_by_user_id = account.updated_by_user_id
            row.updated_at = now
            changed += 1
    await session.flush()
    return changed
