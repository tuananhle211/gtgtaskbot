"""The account screen: who I am, my profile, my team's figures, password resets.

Authority lives here, not in the router:

* **the member list** - the OWNER and the ADMIN see everyone (and, with
  ``include_inactive``, the deactivated accounts too); the ORD head
  ("Trưởng phòng ORD") sees the ORD members. Anybody else is refused with
  ``account_members_forbidden``;
* **a password reset** - the OWNER for anyone but themselves; an ADMIN for
  anyone but themselves and an OWNER;
* **deactivate / reactivate** - the OWNER and the ADMIN, never on themselves
  nor on an OWNER (``account_status_forbidden``). Deactivating closes every web
  session of the account. A reset gives the account
  a random temporary password sent to the person's Telegram (the same flow as
  "Quên mật khẩu?", :mod:`~meobot.application.account.password_reset_service`),
  clears the lockout and signs the person out everywhere.

A user with no open ``org_unit_members`` row belongs to no stream; their row
lists no unit.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.account.avatar_service import avatar_urls
from meobot.application.account.password_reset_service import PasswordResetService
from meobot.application.account.stats_service import AccountStatsService, MemberStats, Month
from meobot.application.audit_service import AuditService
from meobot.application.units.directory import manager_fits
from meobot.application.web_auth_service import WebAuthService
from meobot.core.time import ensure_utc, utcnow
from meobot.db.models.order import Order, OrderNode
from meobot.db.models.org_unit import OrgUnit, OrgUnitMember
from meobot.db.models.user import User
from meobot.db.models.user_avatar import UserAvatar
from meobot.db.models.web_session import WebSession, WebSessionKind
from meobot.domain.access.models import UserStatus
from meobot.domain.account.errors import (
    AccountMembersForbiddenError,
    AccountNotFoundError,
    AccountStatusForbiddenError,
    AccountValidationError,
    MemberDeleteForbiddenError,
    PasswordResetForbiddenError,
    PasswordResetUndeliverableError,
)
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.identity.labels import role_label
from meobot.domain.identity.models import Actor, Role
from meobot.domain.orders.models import ACTIVE_NODE_STATUSES, TERMINAL_STAGES
from meobot.domain.units.labels import unit_role_label
from meobot.domain.units.models import FUNCTION_ROLES, UnitCode, UnitMemberRole, UnitMembership

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
    #: The viewer has a team of their own ("Thành viên nhóm tôi").
    has_team: bool = False
    #: The viewer may also list everybody they are shown ("Tất cả").
    can_view_all: bool = False
    #: Whether this listing is the viewer's team.
    team: bool = False


@dataclass(frozen=True, slots=True)
class _Visibility:
    everyone: bool
    units: frozenset[UnitCode]


def _visibility(actor: Actor, membership: UnitMembership) -> _Visibility | None:
    if actor.role in (Role.OWNER, Role.ADMIN):
        return _Visibility(everyone=True, units=frozenset(UnitCode))
    units: set[UnitCode] = set()
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
        include_inactive: bool = False,
        team: bool = False,
    ) -> MemberListing:
        """The roster with the month's figures.

        ``team=True`` ("Thành viên nhóm tôi") lists the viewer's own team: their
        Leader (head) and the people who report to the same one - open to every
        ORD member, with the account-security columns blanked for somebody who
        is not shown the full roster. A viewer with no team gets the full
        roster instead, if they may see it.
        """
        visibility = _visibility(actor, membership)
        mine = await self._team_of(actor, membership)
        team = team and mine is not None
        if visibility is None and not team:
            raise AccountMembersForbiddenError()
        if include_inactive and actor.role not in (Role.OWNER, Role.ADMIN):
            raise AccountMembersForbiddenError()
        wanted = None if team else self._parse_unit_filter(unit)
        if wanted is not None and (visibility is None or wanted not in visibility.units):
            raise AccountMembersForbiddenError()

        # A deleted account (0054) is listed nowhere.
        statement = select(User).where(User.deleted_at.is_(None)).order_by(User.full_name, User.id)
        if not include_inactive:
            statement = statement.where(User.active.is_(True))
        users = (await self._session.scalars(statement)).all()
        units_by_user = await self._units_of([user.id for user in users])

        listed: list[tuple[User, tuple[MemberUnit, ...]]] = []
        for user in users:
            units = units_by_user.get(user.id, ())
            codes = {item.code for item in units}
            if team:
                assert mine is not None
                if user.id not in mine:
                    continue
            elif wanted is not None:
                if wanted not in codes:
                    continue
            elif visibility is None or (not visibility.everyone and not codes & visibility.units):
                continue
            listed.append((user, units))

        ids = [user.id for user, _ in listed]
        stats = await self._stats.stats_for(ids, month)
        last_logins = await self._last_logins(ids)
        avatars = await avatar_urls(self._session, ids)
        now = utcnow()
        # Sign-in details are for whoever is shown the full roster.
        private = visibility is None
        return MemberListing(
            month=month,
            has_team=mine is not None,
            can_view_all=visibility is not None,
            team=team,
            members=[
                MemberView(
                    user=user,
                    units=units,
                    role_label=member_role_label(user, units),
                    last_login_at=None if private else last_logins.get(user.id),
                    has_custom_password=not private
                    and user.password_hash is not None
                    and not user.password_temporary,
                    password_temporary=not private and bool(user.password_temporary),
                    locked=not private
                    and user.locked_until is not None
                    and ensure_utc(user.locked_until) > now,
                    stats=stats[user.id],
                    avatar_url=avatars.get(user.id),
                )
                for user, units in listed
            ],
        )

    async def _team_of(self, actor: Actor, membership: UnitMembership) -> set[uuid.UUID] | None:
        """The viewer's own ORD team: their Leader (an orderer: head) and
        everybody who reports to the same one, themselves included.

        A Leader's (head's) team is the staff whose own Leader they are, plus
        the staff with none set - those belong to every Leader of the ban.
        Staff with no Leader set see the whole ban. ``None``: no ORD team.
        """
        entry = membership.entry(UnitCode.ADS)
        if entry is None or actor.user_id is None:
            return None
        rows = (
            await self._session.scalars(
                select(OrgUnitMember)
                .join(User, User.id == OrgUnitMember.user_id)
                .where(
                    OrgUnitMember.unit_id == entry.unit_id,
                    OrgUnitMember.left_at.is_(None),
                    User.active.is_(True),
                    User.deleted_at.is_(None),
                )
            )
        ).all()
        by_user = {row.user_id: row for row in rows}
        me = by_user.get(actor.user_id)
        if me is None:
            return None

        def boss_of(row: OrgUnitMember) -> uuid.UUID | None:
            boss = None if row.manager_user_id is None else by_user.get(row.manager_user_id)
            return boss.user_id if boss is not None and manager_fits(boss, row) else None

        def leads(row: OrgUnitMember) -> bool:
            return row.role is UnitMemberRole.HEAD or (row.role in FUNCTION_ROLES and row.is_lead)

        staff_role: UnitMemberRole
        if leads(me):
            bosses = {me.user_id}
            staff_role = UnitMemberRole.ORDERER if me.role is UnitMemberRole.HEAD else me.role
        elif me.role is UnitMemberRole.ORDERER or me.role in FUNCTION_ROLES:
            staff_role = me.role
            own = boss_of(me)
            bosses = (
                {own}
                if own is not None
                else {
                    row.user_id
                    for row in rows
                    if leads(row)
                    and (
                        row.role is UnitMemberRole.HEAD
                        if staff_role is UnitMemberRole.ORDERER
                        else row.role is staff_role
                    )
                }
            )
        else:
            return None
        found = {me.user_id, *bosses}
        for row in rows:
            if row.role is not staff_role or leads(row):
                continue
            boss = boss_of(row)
            if boss is None or boss in bosses:
                found.add(row.user_id)
        return found

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
            AccountNotFoundError: no such account.
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
        if actor.role is Role.ADMIN and target.role is Role.OWNER:
            raise PasswordResetForbiddenError()

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

    # --- status ---------------------------------------------------------------------

    async def set_active(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        user_id: uuid.UUID,
        active: bool,
    ) -> User:
        """Deactivate (``active=False``) or reactivate an account.

        The same lifecycle the PR members screen and ``/suspend_user`` write
        (status ``SUSPENDED`` <-> ``ACTIVE``), stream-neutral and open to the
        ADMIN as well as the OWNER. Deactivating closes every web session of
        the account. A no-op when the account is already in that state.

        Raises:
            AccountStatusForbiddenError: not OWNER/ADMIN, on oneself, or on an
                OWNER account.
            AccountNotFoundError: no such account.
            AccountValidationError: ``account_revoked`` - a revoked account is
                restored deliberately elsewhere, never reactivated here.
        """
        if actor.role not in (Role.OWNER, Role.ADMIN):
            raise AccountStatusForbiddenError()
        if actor.user_id == user_id:
            raise AccountStatusForbiddenError(
                "Bạn không thể tự vô hiệu hoá hoặc kích hoạt tài khoản của chính mình."
            )
        target = await self._session.get(User, user_id)
        if target is None:
            raise AccountNotFoundError()
        if target.role is Role.OWNER:
            raise AccountStatusForbiddenError("Không thể thay đổi trạng thái tài khoản Chủ sở hữu.")
        if active and target.status is UserStatus.REVOKED:
            raise AccountValidationError(
                "account_revoked",
                "Tài khoản này đã bị loại khỏi hệ thống và không thể kích hoạt lại tại đây.",
            )
        already = (
            target.active and target.status is UserStatus.ACTIVE if active else not target.active
        )
        if already:
            return target
        before = target.status
        now = utcnow()
        revoked = 0
        if active:
            target.status = UserStatus.ACTIVE
            target.active = True
            target.suspended_at = None
            target.suspended_by_user_id = None
            target.status_reason = None
        else:
            target.status = UserStatus.SUSPENDED
            target.active = False
            target.suspended_at = now
            target.suspended_by_user_id = actor.user_id
        target.last_status_changed_at = now
        await self._session.flush()
        if not active:
            revoked = await self._auth.revoke_all_for_user(user_id=target.id)
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=(AuditAction.USER_ENABLED if active else AuditAction.USER_SUSPENDED).value,
            result=AuditResult.SUCCESS,
            entity_type="user",
            entity_id=str(target.id),
            before_data={"status": before.value if before is not None else None},
            after_data={
                "status": target.status.value,
                "active": target.active,
                "via": "account",
                "sessions_revoked": revoked,
            },
        )
        return target

    async def delete_member(
        self, *, actor: Actor, request_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        """ "Xoá thành viên": remove an account for good. The OWNER only.

        The row stays - the orders they placed and the work they did keep
        their history, named "<tên> (đã xoá)" - but nothing of the account
        does: it cannot sign in (web sessions closed, password and Telegram
        identity cleared, so the same Telegram account may be invited afresh),
        every tag is closed and it is listed nowhere. Open work is handed
        over first.

        Raises:
            MemberDeleteForbiddenError: not the OWNER, oneself, or an OWNER.
            AccountNotFoundError: no such account (or already deleted).
            AccountValidationError: ``member_has_open_work``.
        """
        if actor.role is not Role.OWNER:
            raise MemberDeleteForbiddenError()
        if actor.user_id == user_id:
            raise MemberDeleteForbiddenError("Bạn không thể tự xoá tài khoản của chính mình.")
        target = await self._session.get(User, user_id)
        if target is None or target.deleted_at is not None:
            raise AccountNotFoundError()
        if target.role is Role.OWNER:
            raise MemberDeleteForbiddenError("Không thể xoá tài khoản Chủ sở hữu.")
        if await self._holds_open_work(user_id):
            raise AccountValidationError(
                "member_has_open_work",
                "Thành viên này còn order hoặc công đoạn đang mở. Bàn giao xong rồi xoá.",
            )
        now = utcnow()
        before: dict[str, object] = {"full_name": target.full_name, "role": target.role.value}
        tags = (
            await self._session.scalars(
                select(OrgUnitMember).where(
                    OrgUnitMember.user_id == user_id, OrgUnitMember.left_at.is_(None)
                )
            )
        ).all()
        for tag in tags:
            tag.left_at = now
            tag.manager_user_id = None
        # Whoever reported to them falls back to the whole ban.
        await self._session.execute(
            update(OrgUnitMember)
            .where(OrgUnitMember.manager_user_id == user_id)
            .values(manager_user_id=None)
        )
        await self._session.execute(delete(UserAvatar).where(UserAvatar.user_id == user_id))
        target.deleted_at = now
        target.active = False
        target.status = UserStatus.REVOKED
        target.revoked_at = now
        target.revoked_by_user_id = actor.user_id
        target.last_status_changed_at = now
        target.full_name = f"{target.full_name[:190]} (đã xoá)"
        target.telegram_user_id = None
        target.telegram_username = None
        target.telegram_private_chat_id = None
        target.private_chat_available = False
        target.password_hash = None
        await self._session.flush()
        revoked = await self._auth.revoke_all_for_user(user_id=target.id)
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.USER_DELETED.value,
            result=AuditResult.SUCCESS,
            entity_type="user",
            entity_id=str(target.id),
            before_data=before,
            after_data={
                "deleted_at": now.isoformat(),
                "tags_closed": len(tags),
                "sessions_revoked": revoked,
            },
        )

    async def _holds_open_work(self, user_id: uuid.UUID) -> bool:
        """An ORD order of theirs still running, or a step they hold."""
        closed = sorted(TERMINAL_STAGES, key=lambda stage: stage.value)
        owns = await self._session.scalar(
            select(Order.id)
            .where(Order.owner_user_id == user_id, Order.stage.notin_(closed))
            .limit(1)
        )
        if owns is not None:
            return True
        holds = await self._session.scalar(
            select(OrderNode.id)
            .join(Order, Order.id == OrderNode.order_id)
            .where(
                OrderNode.assignee_user_id == user_id,
                OrderNode.status.in_(sorted(ACTIVE_NODE_STATUSES, key=lambda s: s.value)),
                Order.stage.notin_(closed),
            )
            .limit(1)
        )
        return holds is not None

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
        """Each person's open tags (none when untagged). One query."""
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
        units: dict[uuid.UUID, list[MemberUnit]] = defaultdict(list)
        for user_id, code, role, is_lead, member_code, left_at in rows:
            if left_at is None:
                units[user_id].append(
                    MemberUnit(
                        code=UnitCode(code),
                        role=role,
                        is_lead=bool(is_lead),
                        member_code=member_code,
                    )
                )
        return {user_id: tuple(units.get(user_id, ())) for user_id in user_ids}

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
