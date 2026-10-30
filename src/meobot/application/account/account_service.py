"""The account screen: who I am, my profile, my team's figures, password resets.

Authority lives here, not in the router:

* **the member list** - the OWNER and the ADMIN see everyone (and, with
  ``include_inactive``, the deactivated accounts too); the ORD head
  ("Trưởng phòng ADS") sees the ORD members. Anybody else is refused with
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
from meobot.application.account.member_code import write_member_code
from meobot.application.account.password_reset_service import PasswordResetService
from meobot.application.account.stats_service import AccountStatsService, MemberStats, Month
from meobot.application.audit_service import AuditService
from meobot.application.org.access import Access
from meobot.application.org.departments import department_people, head_team
from meobot.application.org.seats import load_seats
from meobot.application.units.directory import manager_fits
from meobot.application.user_service import UserService
from meobot.application.web_auth_service import IssuedLogin, WebAuthService
from meobot.core.time import ensure_utc, utcnow
from meobot.db.models.department import DepartmentMember
from meobot.db.models.order import Order, OrderNode
from meobot.db.models.org_unit import OrgUnit, OrgUnitMember
from meobot.db.models.user import User
from meobot.db.models.user_avatar import UserAvatar
from meobot.db.models.web_session import WebSession, WebSessionKind
from meobot.domain.access.models import UserStatus
from meobot.domain.account.errors import (
    AccountMembersForbiddenError,
    AccountNotFoundError,
    AccountProfileForbiddenError,
    AccountStatusForbiddenError,
    AccountValidationError,
    ImpersonationForbiddenError,
    MemberCodeForbiddenError,
    MemberDeleteForbiddenError,
    PasswordResetForbiddenError,
    PasswordResetUndeliverableError,
)
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.identity.labels import role_label
from meobot.domain.identity.models import Actor, Role
from meobot.domain.orders.models import ACTIVE_NODE_STATUSES, TERMINAL_STAGES
from meobot.domain.org.models import Position, Seat
from meobot.domain.performance.models import MODULE_PERFORMANCE, PerformancePermission
from meobot.domain.units.labels import unit_role_label
from meobot.domain.units.member_code import normalise_member_code
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
    #: The person's open seats ("Phòng ban"), in the departments' order.
    seats: tuple[Seat, ...] = ()


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
    #: A department head's own people (``head_team``), plus themselves.
    people: frozenset[uuid.UUID] = frozenset()


async def _visibility(
    session: AsyncSession, actor: Actor, membership: UnitMembership
) -> _Visibility | None:
    """OWNER / ADMIN see everyone; the Trưởng phòng ORD the ORD people; a
    department head (2026-10-17) the members who report to them."""
    if actor.role in (Role.OWNER, Role.ADMIN):
        return _Visibility(everyone=True, units=frozenset(UnitCode))
    units: set[UnitCode] = set()
    ads = membership.entry(UnitCode.ADS)
    if ads is not None and ads.role is UnitMemberRole.HEAD:
        units.add(UnitCode.ADS)
    heads = any(seat.position is Position.HEAD for seat in membership.seats)
    if not units and not heads:
        return None
    people: frozenset[uuid.UUID] = frozenset()
    if heads and actor.user_id is not None:
        people = await head_team(session, actor.user_id) | {actor.user_id}
    return _Visibility(everyone=False, units=frozenset(units), people=people)


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
        visibility = await _visibility(self._session, actor, membership)
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
        seats_by_user = await load_seats(self._session, [user.id for user in users])

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
            elif visibility is None or (
                not visibility.everyone
                and not codes & visibility.units
                and user.id not in visibility.people
            ):
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
                    seats=seats_by_user.get(user.id, ()),
                )
                for user, units in listed
            ],
        )

    async def profile(
        self, *, actor: Actor, membership: UnitMembership, user_id: uuid.UUID, month: Month
    ) -> MemberView:
        """One person's page (2026-10-19): their card and the month's figures.

        Who may open it: the OWNER / an ADMIN anyone's; a department head
        everybody seated in a department they head (reporting to them or
        not); everybody their own. Anybody else is refused (403
        ``account_profile_forbidden``) - and so is a page they may not see,
        rather than 404, so a link never says whether the account exists.
        """
        if not await self.may_view_profile(actor, membership, user_id):
            raise AccountProfileForbiddenError()
        user = await self._session.get(User, user_id)
        if user is None or user.deleted_at is not None:
            raise AccountNotFoundError()
        units = (await self._units_of([user.id])).get(user.id, ())
        seats = (await load_seats(self._session, [user.id])).get(user.id, ())
        stats = await self._stats.stats_for_one(user.id, month)
        avatars = await avatar_urls(self._session, [user.id])
        now = utcnow()
        return MemberView(
            user=user,
            units=units,
            role_label=member_role_label(user, units),
            last_login_at=(await self._last_logins([user.id])).get(user.id),
            has_custom_password=user.password_hash is not None and not user.password_temporary,
            password_temporary=bool(user.password_temporary),
            locked=user.locked_until is not None and ensure_utc(user.locked_until) > now,
            stats=stats,
            avatar_url=avatars.get(user.id),
            seats=seats,
        )

    async def may_view_profile(
        self, actor: Actor, membership: UnitMembership, user_id: uuid.UUID
    ) -> bool:
        if actor.role in (Role.OWNER, Role.ADMIN):
            return True
        if actor.user_id is None:
            return False
        if user_id == actor.user_id:
            return True
        access = membership.access
        if (
            isinstance(access, Access)
            and access.scope(MODULE_PERFORMANCE, PerformancePermission.VIEW.value) == "ALL"
        ):
            return True
        if not any(seat.position is Position.HEAD for seat in membership.seats):
            return False
        return user_id in await department_people(self._session, actor.user_id)

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

    # --- impersonation ("đăng nhập thay", 2026-10-21) --------------------------------

    async def issue_login_link_for(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        telegram_user_id: int | None = None,
    ) -> IssuedLogin:
        """Mint a single-use login link **for another account**, so the OWNER
        can sign in as that person to help or fix something.

        The target is named by its ``users`` id or - what the OWNER actually
        has to hand - the person's Telegram id (exactly one of the two).

        The same single-use, minutes-long link ``/web`` gives a person
        themselves (:meth:`WebAuthService.issue_login_link`) - not a hidden
        back door: OWNER only, audited every time (who, into whom, when; never
        the URL or token), and it mints an ordinary session that the usual
        rules and the audit trail still apply to.

        Raises:
            ImpersonationForbiddenError: the caller is not the OWNER, or the
                target is themselves or another OWNER account.
            AccountValidationError: neither a user id nor a Telegram id, or both.
            AccountNotFoundError: no such account, or a deleted one.
            AuthorizationError: the target cannot sign in (deactivated).
            ConfigurationError: ``WEB_BASE_URL`` is unset.
        """
        if actor.role is not Role.OWNER:
            raise ImpersonationForbiddenError()
        if (user_id is None) == (telegram_user_id is None):
            raise AccountValidationError(
                "target_required",
                "Cần đúng một trong hai: id tài khoản hoặc id Telegram.",
                details={"field": "telegram_user_id"},
            )
        if user_id is not None:
            target = await self._session.get(User, user_id)
        else:
            target = await self._session.scalar(
                select(User).where(User.telegram_user_id == telegram_user_id)
            )
        if target is None or target.deleted_at is not None:
            raise AccountNotFoundError()
        if actor.user_id == target.id:
            raise ImpersonationForbiddenError("Đăng nhập tài khoản của bạn thì dùng /web trên bot.")
        if target.role is Role.OWNER:
            raise ImpersonationForbiddenError(
                "Không tạo được liên kết đăng nhập thay cho tài khoản chủ sở hữu."
            )
        issued = await self._auth.issue_login_link(user_id=target.id)
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.WEB_LOGIN_LINK_ISSUED_FOR.value,
            result=AuditResult.SUCCESS,
            entity_type="web_session",
            entity_id=str(target.id),
            # The URL is a working credential; the audit row only records that
            # one was minted, for whom, and when it expires - never the token.
            after_data={
                "target_user_id": str(target.id),
                "expires_at": issued.expires_at.isoformat(),
            },
        )
        return issued

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

    async def set_member_code(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        user_id: uuid.UUID,
        member_code: str | None,
    ) -> str | None:
        """*Mã nhân viên* of any account, in a stream or not (0063). The
        OWNER and the ADMIN only. Folded like every code (A-Z0-9, at most 12;
        blank clears it) and written on the account and every open tag.
        Returns the code as stored.

        Raises:
            MemberCodeForbiddenError: not OWNER/ADMIN.
            AccountNotFoundError: no such account, or a deleted one.
        """
        if actor.role not in (Role.OWNER, Role.ADMIN):
            raise MemberCodeForbiddenError()
        target = await self._session.get(User, user_id)
        if target is None or target.deleted_at is not None:
            raise AccountNotFoundError()
        code = normalise_member_code(member_code)
        before = target.member_code
        if code == before:
            return code
        await write_member_code(self._session, target, code)
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.USER_MEMBER_CODE_UPDATED.value,
            result=AuditResult.SUCCESS,
            entity_type="user",
            entity_id=str(target.id),
            before_data={"member_code": before},
            after_data={"member_code": code},
        )
        return code

    async def restore_member(
        self, *, actor: Actor, request_id: uuid.UUID, user_id: uuid.UUID
    ) -> User:
        """ "Khôi phục tài khoản" (2026-10-30): undo a revocation. The OWNER only
        (a revoked account was ended on purpose); never oneself, never a deleted
        account. ``UserService.restore`` does it and audits it
        (``user.enabled``, reason "restored").

        Raises:
            AccountStatusForbiddenError: not the OWNER, or on oneself.
            AccountNotFoundError: no such account (or deleted).
            AccountValidationError: ``account_not_revoked``.
        """
        if actor.role is not Role.OWNER:
            raise AccountStatusForbiddenError("Chỉ Chủ sở hữu khôi phục được tài khoản đã thu hồi.")
        if actor.user_id == user_id:
            raise AccountStatusForbiddenError()
        target = await self._session.get(User, user_id)
        if target is None or target.deleted_at is not None:
            raise AccountNotFoundError()
        if target.status is not UserStatus.REVOKED:
            raise AccountValidationError(
                "account_not_revoked", "Tài khoản này không ở trạng thái đã thu hồi."
            )
        return await UserService(self._session, self._audit).restore(
            actor=actor, request_id=request_id, user_id=user_id
        )

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
        # 0061: their seats close too, and nobody reports to them any more.
        await self._session.execute(
            update(DepartmentMember)
            .where(DepartmentMember.user_id == user_id, DepartmentMember.left_at.is_(None))
            .values(left_at=now, manager_user_id=None)
        )
        await self._session.execute(
            update(DepartmentMember)
            .where(DepartmentMember.manager_user_id == user_id)
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
