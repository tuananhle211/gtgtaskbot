"""The account screen: who I am, my profile, my team's figures, password resets.

Authority lives here, not in the router:

* **the member list** - the OWNER sees everyone; an ADMIN sees the members of
  the units they are tagged in; the Ads HEAD sees the Ads members. Anybody else
  is refused with ``account_members_forbidden``;
* **a password reset** - the OWNER for anyone but themselves; an ADMIN for
  somebody in one of their units who is not an OWNER. A reset gives the account
  a random temporary password sent to the person's Telegram (the same flow as
  "Quên mật khẩu?", :mod:`~meobot.application.account.password_reset_service`),
  clears the lockout and signs the person out everywhere.

Unit membership follows the units module's legacy rule: a user with no
``org_unit_members`` row at all is a PR member; a user whose rows are all
closed belongs to no unit.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.account.avatar_service import avatar_urls
from meobot.application.account.password_reset_service import PasswordResetService
from meobot.application.account.stats_service import AccountStatsService, MemberStats, Month
from meobot.application.audit_service import AuditService
from meobot.application.web_auth_service import WebAuthService
from meobot.core.time import ensure_utc, utcnow
from meobot.db.models.org_unit import OrgUnit, OrgUnitMember
from meobot.db.models.user import User
from meobot.db.models.web_session import WebSession, WebSessionKind
from meobot.domain.account.errors import (
    AccountMembersForbiddenError,
    AccountNotFoundError,
    AccountValidationError,
    PasswordResetForbiddenError,
    PasswordResetUndeliverableError,
)
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.identity.labels import role_label
from meobot.domain.identity.models import Actor, Role
from meobot.domain.units.labels import unit_role_label
from meobot.domain.units.models import UnitCode, UnitMemberRole, UnitMembership

FULL_NAME_MIN = 2
FULL_NAME_MAX = 80


@dataclass(frozen=True, slots=True)
class MemberUnit:
    code: UnitCode
    role: UnitMemberRole
    is_lead: bool = False
    member_code: str | None = None


@dataclass(frozen=True, slots=True)
class MemberView:
    """One row of the member list."""

    user: User
    units: tuple[MemberUnit, ...]
    role_label: str
    last_login_at: datetime | None
    has_custom_password: bool
    locked: bool
    stats: MemberStats
    password_temporary: bool = False
    #: 0047: the picture's URL, ``None`` without one.
    avatar_url: str | None = None


@dataclass(frozen=True, slots=True)
class MemberListing:
    month: Month
    members: list[MemberView] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _Visibility:
    everyone: bool
    units: frozenset[UnitCode]


def _visibility(actor: Actor, membership: UnitMembership) -> _Visibility | None:
    if actor.role is Role.OWNER:
        return _Visibility(everyone=True, units=frozenset(UnitCode))
    units: set[UnitCode] = set()
    if actor.role is Role.ADMIN:
        units |= {entry.unit_code for entry in membership.entries}
    ads = membership.entry(UnitCode.ADS)
    if ads is not None and ads.role is UnitMemberRole.HEAD:
        units.add(UnitCode.ADS)
    return _Visibility(everyone=False, units=frozenset(units)) if units else None


def member_role_label(user: User, units: Iterable[MemberUnit]) -> str:
    """What a person is, in one phrase: the global role for OWNER/ADMIN, else
    their Ads position(s) and/or their PR role."""
    if user.role in (Role.OWNER, Role.ADMIN):
        return role_label(user.role)
    labels: list[str] = []
    for unit in units:
        label = (
            role_label(user.role)
            if unit.code is UnitCode.PR
            else unit_role_label(unit.role, unit.is_lead)
        )
        if label not in labels:
            labels.append(label)
    return " · ".join(labels) if labels else role_label(user.role)


class AccountService:
    """Reads and the few writes behind ``/api/account``."""

    def __init__(
        self,
        session: AsyncSession,
        audit: AuditService,
        auth: WebAuthService,
        stats: AccountStatsService,
        resets: PasswordResetService,
    ) -> None:
        self._session = session
        self._audit = audit
        self._auth = auth
        self._stats = stats
        self._resets = resets

    # --- me ----------------------------------------------------------------------

    async def user_for(self, actor: Actor) -> User:
        if actor.user_id is None:
            raise AccountNotFoundError()
        user = await self._session.get(User, actor.user_id)
        if user is None:
            raise AccountNotFoundError()
        return user

    async def stats(self, actor: Actor, month: Month) -> MemberStats:
        user = await self.user_for(actor)
        return await self._stats.stats_for_one(user.id, month)

    async def update_profile(self, *, actor: Actor, request_id: uuid.UUID, full_name: str) -> User:
        """Rename oneself. 2-80 characters after trimming; inner spaces collapsed."""
        cleaned = " ".join(full_name.split())
        if not FULL_NAME_MIN <= len(cleaned) <= FULL_NAME_MAX:
            raise AccountValidationError(
                "full_name_invalid",
                f"Họ tên cần từ {FULL_NAME_MIN} đến {FULL_NAME_MAX} ký tự.",
                details={"field": "full_name"},
            )
        user = await self.user_for(actor)
        before = user.full_name
        if cleaned != before:
            user.full_name = cleaned
            await self._session.flush()
            await self._audit.record_action(
                request_id=request_id,
                actor=actor,
                action=AuditAction.USER_PROFILE_UPDATED,
                result=AuditResult.SUCCESS,
                entity_type="user",
                entity_id=str(user.id),
                before_data={"full_name": before},
                after_data={"full_name": cleaned},
            )
        return user

    # --- members -------------------------------------------------------------------

    async def members(
        self,
        *,
        actor: Actor,
        membership: UnitMembership,
        month: Month,
        unit: str | None,
    ) -> MemberListing:
        visibility = _visibility(actor, membership)
        if visibility is None:
            raise AccountMembersForbiddenError()
        wanted = self._parse_unit_filter(unit)
        if wanted is not None and wanted not in visibility.units:
            raise AccountMembersForbiddenError()

        users = (
            await self._session.scalars(
                select(User).where(User.active.is_(True)).order_by(User.full_name, User.id)
            )
        ).all()
        units_by_user = await self._units_of([user.id for user in users])

        listed: list[tuple[User, tuple[MemberUnit, ...]]] = []
        for user in users:
            units = units_by_user.get(user.id, ())
            codes = {item.code for item in units}
            if wanted is not None:
                if wanted not in codes:
                    continue
            elif not visibility.everyone and not codes & visibility.units:
                continue
            listed.append((user, units))

        ids = [user.id for user, _ in listed]
        stats = await self._stats.stats_for(ids, month)
        last_logins = await self._last_logins(ids)
        avatars = await avatar_urls(self._session, ids)
        now = utcnow()
        return MemberListing(
            month=month,
            members=[
                MemberView(
                    user=user,
                    units=units,
                    role_label=member_role_label(user, units),
                    last_login_at=last_logins.get(user.id),
                    has_custom_password=(
                        user.password_hash is not None and not user.password_temporary
                    ),
                    password_temporary=bool(user.password_temporary),
                    locked=user.locked_until is not None and ensure_utc(user.locked_until) > now,
                    stats=stats[user.id],
                    avatar_url=avatars.get(user.id),
                )
                for user, units in listed
            ],
        )

    # --- reset -----------------------------------------------------------------------

    async def reset_password(
        self,
        *,
        actor: Actor,
        membership: UnitMembership,
        request_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> int:
        """A temporary password sent to the member's Telegram, lock cleared,
        signed out everywhere.

        Returns how many sessions were closed.

        Raises:
            PasswordResetForbiddenError: not OWNER/ADMIN, an ADMIN aiming at an
                OWNER, or anybody aiming at themselves (use the change form).
            AccountNotFoundError: no such account, or (for an ADMIN) one outside
                every unit they are tagged in.
            PasswordResetUndeliverableError: MeoBot cannot DM the member (no
                Telegram id, or they never started the bot); nothing changes.
        """
        if actor.role not in (Role.OWNER, Role.ADMIN):
            raise PasswordResetForbiddenError()
        if actor.user_id == user_id:
            raise PasswordResetForbiddenError(
                "Hãy dùng chức năng đổi mật khẩu cho tài khoản của bạn."
            )
        target = await self._session.get(User, user_id)
        if target is None:
            raise AccountNotFoundError()
        if actor.role is Role.ADMIN:
            if target.role is Role.OWNER:
                raise PasswordResetForbiddenError()
            mine = {entry.unit_code for entry in membership.entries}
            theirs = {unit.code for unit in (await self._units_of([target.id])).get(target.id, ())}
            if not mine & theirs:
                raise AccountNotFoundError()

        chat_id = await self._resets.private_chat_of(target)
        if chat_id is None:
            raise PasswordResetUndeliverableError()
        revoked = await self._resets.issue_temporary(
            target, chat_id=chat_id, created_by_user_id=actor.user_id
        )
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.AUTH_PASSWORD_RESET,
            result=AuditResult.SUCCESS,
            entity_type="user",
            entity_id=str(target.id),
            after_data={"sessions_revoked": revoked, "temporary_password_sent": True},
        )
        return revoked

    # --- internals ----------------------------------------------------------------------

    @staticmethod
    def _parse_unit_filter(value: str | None) -> UnitCode | None:
        if value is None or value.strip().upper() in ("", "ALL"):
            return None
        try:
            return UnitCode(value.strip().upper())
        except ValueError as error:
            raise AccountValidationError(
                "invalid_unit", "Phòng không hợp lệ.", details={"field": "unit"}
            ) from error

    async def _units_of(self, user_ids: list[uuid.UUID]) -> dict[uuid.UUID, tuple[MemberUnit, ...]]:
        """Each person's open tags, with the legacy rule. One query."""
        if not user_ids:
            return {}
        rows = (
            await self._session.execute(
                select(
                    OrgUnitMember.user_id,
                    OrgUnit.code,
                    OrgUnitMember.role,
                    OrgUnitMember.is_lead,
                    OrgUnitMember.member_code,
                    OrgUnitMember.left_at,
                )
                .join(OrgUnit, OrgUnit.id == OrgUnitMember.unit_id)
                .where(OrgUnitMember.user_id.in_(user_ids))
                .order_by(OrgUnit.code)
            )
        ).all()
        has_rows: set[uuid.UUID] = set()
        units: dict[uuid.UUID, list[MemberUnit]] = defaultdict(list)
        for user_id, code, role, is_lead, member_code, left_at in rows:
            has_rows.add(user_id)
            if left_at is None:
                units[user_id].append(
                    MemberUnit(
                        code=UnitCode(code),
                        role=role,
                        is_lead=bool(is_lead),
                        member_code=member_code,
                    )
                )
        legacy = (MemberUnit(code=UnitCode.PR, role=UnitMemberRole.MEMBER),)
        return {
            user_id: (tuple(units[user_id]) if user_id in has_rows else legacy)
            for user_id in user_ids
        }

    async def _last_logins(self, user_ids: list[uuid.UUID]) -> dict[uuid.UUID, datetime]:
        if not user_ids:
            return {}
        rows = (
            await self._session.execute(
                select(WebSession.user_id, func.max(WebSession.created_at))
                .where(
                    WebSession.user_id.in_(user_ids),
                    WebSession.kind == WebSessionKind.SESSION,
                )
                .group_by(WebSession.user_id)
            )
        ).all()
        return {user_id: ensure_utc(at) for user_id, at in rows if at is not None}


__all__ = [
    "FULL_NAME_MAX",
    "FULL_NAME_MIN",
    "AccountService",
    "MemberListing",
    "MemberUnit",
    "MemberView",
    "member_role_label",
]
