"""FastAPI dependencies (dependency injection surface for routes).

Routes never build services themselves and never contain business logic; they
declare what they need here and delegate.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.account.account_service import AccountService
from meobot.application.account.avatar_service import AvatarService
from meobot.application.account.password_reset_service import PasswordResetService
from meobot.application.account.password_service import PasswordService
from meobot.application.account.stats_service import AccountStatsService
from meobot.application.audit_service import AuditService
from meobot.application.chat_memory_service import ChatMemoryService
from meobot.application.drive_folder_service import DriveFolderService
from meobot.application.health_service import HealthService
from meobot.application.invite_service import InviteService
from meobot.application.pr_services import PrServices, build_pr_services
from meobot.application.script_review_service import ScriptReviewService
from meobot.application.script_service import ScriptService
from meobot.application.script_sync_service import ScriptSyncService
from meobot.application.script_type_service import ScriptTypeService
from meobot.application.sheet_inspection_service import SheetInspectionService
from meobot.application.sheet_profile_service import SheetProfileService
from meobot.application.sheet_template_service import SheetTemplateService
from meobot.application.spreadsheet_creation_service import SpreadsheetCreationService
from meobot.application.units.directory import NOT_VISIBLE, UnitDirectoryService
from meobot.application.web_auth_service import SESSION_COOKIE, ResolvedSession, WebAuthService
from meobot.core.config import Settings, get_settings
from meobot.core.context import require_request_id
from meobot.core.errors import ConfigurationError
from meobot.db.session import Database
from meobot.domain.account.errors import PasswordChangeRequiredError
from meobot.domain.identity.models import Actor, Role
from meobot.domain.units.errors import UnitNotFoundError
from meobot.domain.units.models import UnitCode, UnitMembership
from meobot.integrations.google.drive import DriveClient
from meobot.integrations.google.sheets import SheetsClient
from meobot.integrations.llm.base import LLMProvider


def get_app_settings(request: Request) -> Settings:
    """The settings this application was built with.

    Reads ``app.state.settings`` - which :func:`~meobot.api.main.create_app`
    always sets - and falls back to the process cache only if something
    constructed an app without going through it.

    It used to return :func:`get_settings` unconditionally, which quietly made
    ``create_app(settings)`` a lie: the app object held one set of settings while
    every route read another. That is invisible until a setting changes
    behaviour a caller can see, and Step 1E has two such settings - the session
    cookie's ``Secure`` flag and the session TTL. A test passing
    ``web_cookie_secure=False`` was getting a ``Secure`` cookie anyway.
    """
    configured: Settings | None = getattr(request.app.state, "settings", None)
    return configured or get_settings()


def get_db(request: Request) -> Database:
    """The process-wide :class:`Database` created during app startup."""
    database: Database = request.app.state.database
    return database


def get_health_service(request: Request) -> HealthService:
    """Health checker assembled at startup."""
    service: HealthService = request.app.state.health_service
    return service


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield a session inside one transaction per request.

    Committed when the route returns normally, rolled back on any exception.
    """
    database: Database = request.app.state.database
    async with database.transaction() as session:
        yield session


def get_request_id() -> uuid.UUID:
    """Correlation id bound by :class:`RequestContextMiddleware`."""
    return uuid.UUID(require_request_id())


def get_current_system_actor(
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> Actor:
    """Synthetic OWNER for the **internal milestone-1 routers only**.

    Those routers carry no authentication: they were written when the API was a
    development tool bound to ``127.0.0.1``, and every request to them is
    attributed to a synthetic OWNER whose Telegram id is the configured bootstrap
    owner.

    **This is never used by the PR web admin.** Step 1E added
    :func:`get_current_web_actor`, which resolves a session cookie to a real
    ``users`` row; a PR route reached with no cookie is refused rather than
    silently granted OWNER.

    Fails closed since Step 1E.1
    ----------------------------

    It now refuses unless ``API_INTERNAL_ROUTERS_ENABLED`` is set - the same flag
    that decides whether those routers are mounted at all. The point is defence
    in depth rather than the primary control: the primary control is that the
    routers are absent, and this makes the failure mode of getting that wrong a
    500 with nothing written, instead of a silent grant of OWNER to an
    unauthenticated caller.

    That matters most for ``PATCH /api/v1/users/{id}/role``. Reached
    unauthenticated as OWNER, it promotes the caller, and because PR capability
    administration is gated on ``user.role.manage``, the whole PR module follows
    from there.

    TODO(milestone-2): move the internal routers onto a real authenticated
    mechanism and delete this.
    """
    if not settings.api_internal_routers_enabled:
        raise ConfigurationError(
            "The synthetic system actor is only available when the internal "
            "api/v1 routers are enabled. A browser-facing route must resolve a "
            "real session - see get_current_web_actor.",
            details={"setting": "API_INTERNAL_ROUTERS_ENABLED"},
        )
    return Actor(
        user_id=None,
        telegram_user_id=settings.meobot_owner_telegram_id,
        telegram_username=None,
        full_name="api-system",
        role=Role.OWNER,
        active=True,
        is_bootstrap_owner=True,
    )


def get_web_auth_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> WebAuthService:
    """Web session issuer/resolver bound to the request's session."""
    return WebAuthService(session, settings)


async def get_current_web_actor(
    request: Request,
    auth: Annotated[WebAuthService, Depends(get_web_auth_service)],
) -> Actor:
    """Resolve the session cookie into a real :class:`Actor`. Fail closed.

    Every PR route depends on this. There is no header, query parameter or
    development bypass that produces an actor - only a cookie that
    :class:`WebAuthService` can match to a live ``web_sessions`` row and an
    active ``users`` row.

    The 401 is deliberately uninformative. "No cookie", "expired", "revoked" and
    "you were deactivated" all read the same to the caller, because the
    difference is only useful to somebody probing.

    Note what this dependency does **not** do: it does not check whether the
    actor may perform the operation. Authority is decided inside the ``Pr*``
    services against the role and the capability grants, so a valid session for
    a new EMPLOYEE authenticates fine and can still do almost nothing.
    """
    resolved = await auth.resolve_web_session(token=request.cookies.get(SESSION_COOKIE))
    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bạn cần đăng nhập lại.",
        )
    request.state.web_session = resolved
    if resolved.must_change_password and not password_change_allows(request.url.path):
        # A session opened with the default (or a temporary) password may only read who it is
        # and change the password. Enforced here, once, for every route that
        # authenticates - never route by route, where one would be forgotten.
        raise PasswordChangeRequiredError()
    return resolved.actor


#: What a default-password session may still reach (0045): the auth routes
#: (session, logout, password login), its own account card, and the change form.
_PASSWORD_CHANGE_ALLOWED_PREFIXES = ("/api/auth/",)
_PASSWORD_CHANGE_ALLOWED_PATHS = frozenset({"/api/account/me", "/api/account/password"})
#: Reading a profile picture (0047) - ``/api/account/avatar/<user_id>``, which
#: only has a GET. Read-only and harmless, and the forced-change card shows
#: the avatar. Uploading/removing (``/api/account/avatar`` itself) stays closed.
_AVATAR_READ_PREFIX = "/api/account/avatar/"


def password_change_allows(path: str) -> bool:
    """Whether a session that must change its password may reach ``path``.

    Exact paths, not prefixes, for the account routes: ``/api/account/me/stats``
    and ``/api/account/members`` stay closed until the password is changed. The
    one pattern is a single segment after ``/api/account/avatar/``.
    """
    if path.startswith(_PASSWORD_CHANGE_ALLOWED_PREFIXES) or path in _PASSWORD_CHANGE_ALLOWED_PATHS:
        return True
    if path.startswith(_AVATAR_READ_PREFIX):
        rest = path[len(_AVATAR_READ_PREFIX) :]
        return bool(rest) and "/" not in rest
    return False


async def get_current_web_session(
    request: Request,
    actor: Annotated[Actor, Depends(get_current_web_actor)],
) -> ResolvedSession | None:
    """The resolved session behind :func:`get_current_web_actor`, if it ran.

    ``None`` when a test replaced the actor dependency: there is no cookie to
    describe then, and the routes treat that as a session with nothing to
    change.
    """
    resolved = getattr(request.state, "web_session", None)
    if isinstance(resolved, ResolvedSession) and resolved.actor.user_id == actor.user_id:
        return resolved
    return None


async def get_optional_web_actor(
    request: Request,
    auth: Annotated[WebAuthService, Depends(get_web_auth_service)],
) -> Actor | None:
    """The session cookie's actor **if one arrived**, and ``None`` otherwise.

    For exactly one kind of endpoint: an **external OAuth callback**, which the
    browser reaches by being redirected from Google or Meta rather than by
    calling MeoBot. Two things make :func:`get_current_web_actor` wrong there:

    * a third-party redirect is a cross-site navigation, so whether the session
      cookie is sent at all depends on ``SameSite`` and on the browser. Making
      the callback depend on it makes a security-critical flow depend on cookie
      policy, which is not where its security lives;
    * the flow already carries its identity. The OAuth **state** was written by
      MeoBot at authorization time and binds the user, the channel, the provider
      and an expiry, single-use. That is the mechanism designed to survive the
      round trip, and the callback should read identity from it.

    So this dependency authenticates *opportunistically*: when a session is
    present it is resolved and handed on as a **defence-in-depth cross-check**
    against the state's own user, and when it is absent the callback still
    proceeds on the strength of the state alone.

    It never raises. A cookie that is expired, revoked or belongs to a
    deactivated account is indistinguishable from no cookie here, which is the
    same non-answer :func:`get_current_web_actor` gives for the same reason.

    **Do not use this on an ordinary route.** Every first-party endpoint keeps
    :data:`CurrentActorDep`; an optional actor there would be an authentication
    bypass rather than a convenience.
    """
    return await auth.resolve_session(token=request.cookies.get(SESSION_COOKIE))


def get_pr_services(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> PrServices:
    """The PR service bundle, on this request's single transaction.

    The same builder the Telegram tools use - see
    :mod:`meobot.application.pr_services`. One wiring, so the two clients cannot
    drift into two different sets of rules.
    """
    return build_pr_services(session, settings)


def get_audit_service(session: Annotated[AsyncSession, Depends(get_session)]) -> AuditService:
    """Audit writer bound to the request's session."""
    return AuditService(session)


def get_script_type_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
) -> ScriptTypeService:
    """Script Type Registry service bound to the request's session."""
    return ScriptTypeService(session, audit)


def get_sheet_profile_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
) -> SheetProfileService:
    """Sheet profile service bound to the request's session."""
    return SheetProfileService(session, audit)


def get_sheets_client(request: Request) -> SheetsClient:
    """Google Sheets client assembled at startup.

    When no credentials are configured this is the not-configured stub, which
    fails loudly on use rather than at import time.
    """
    client: SheetsClient = request.app.state.sheets_client
    return client


def get_llm_provider(request: Request) -> LLMProvider:
    """LLM provider assembled at startup."""
    provider: LLMProvider = request.app.state.llm_provider
    return provider


def get_script_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
) -> ScriptService:
    """Script service bound to the request's session."""
    return ScriptService(session, audit)


def get_script_review_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
    llm: Annotated[LLMProvider, Depends(get_llm_provider)],
) -> ScriptReviewService:
    """Review engine bound to the request's session."""
    return ScriptReviewService(session, audit, llm)


def get_sheet_inspection_service(
    sheets: Annotated[SheetsClient, Depends(get_sheets_client)],
    llm: Annotated[LLMProvider, Depends(get_llm_provider)],
) -> SheetInspectionService:
    """Sheet inspector. Stateless apart from its clients."""
    return SheetInspectionService(sheets, llm)


def get_sheet_sync_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
    sheets: Annotated[SheetsClient, Depends(get_sheets_client)],
    profiles: Annotated[SheetProfileService, Depends(get_sheet_profile_service)],
) -> ScriptSyncService:
    """Synchronisation service bound to the request's session."""
    return ScriptSyncService(session, audit, sheets, profiles)


def get_invite_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
) -> InviteService:
    """Invite service bound to the request's session."""
    return InviteService(session, audit)


def get_drive_client(request: Request) -> DriveClient:
    """Google Drive client assembled at startup.

    When no credentials are configured this is the not-configured stub, which
    fails loudly on use rather than at import time - so the rest of the API
    keeps working without Google.
    """
    client: DriveClient = request.app.state.drive_client
    return client


def get_drive_folder_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
    drive: Annotated[DriveClient, Depends(get_drive_client)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> DriveFolderService:
    """Allowed-folder registry bound to the request's session."""
    return DriveFolderService(session, audit, drive, settings)


def get_sheet_template_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> SheetTemplateService:
    """Sheet template registry bound to the request's session."""
    return SheetTemplateService(session, audit, settings)


def get_spreadsheet_creation_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
    drive: Annotated[DriveClient, Depends(get_drive_client)],
    sheets: Annotated[SheetsClient, Depends(get_sheets_client)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> SpreadsheetCreationService:
    """Spreadsheet creation service bound to the request's session."""
    return SpreadsheetCreationService(session, audit, drive, sheets, settings)


def get_chat_memory_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> ChatMemoryService:
    """Conversation memory bound to the request's session."""
    return ChatMemoryService(session, settings)


def get_account_stats_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AccountStatsService:
    """Per-person monthly figures, on this request's transaction."""
    return AccountStatsService(session)


def get_password_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
    auth: Annotated[WebAuthService, Depends(get_web_auth_service)],
) -> PasswordService:
    """Password login and change, on this request's transaction."""
    return PasswordService(session, settings, audit, auth)


def get_password_reset_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
    auth: Annotated[WebAuthService, Depends(get_web_auth_service)],
) -> PasswordResetService:
    """Temporary passwords sent by Telegram, on this request's transaction."""
    return PasswordResetService(session, settings, audit, auth)


def get_account_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
    auth: Annotated[WebAuthService, Depends(get_web_auth_service)],
    stats: Annotated[AccountStatsService, Depends(get_account_stats_service)],
    resets: Annotated[PasswordResetService, Depends(get_password_reset_service)],
) -> AccountService:
    """The account screen's reads and writes, on this request's transaction."""
    return AccountService(session, audit, auth, stats, resets)


def get_avatar_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
) -> AvatarService:
    """Profile pictures (0047), on this request's transaction."""
    return AvatarService(session, audit)


def get_unit_directory(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UnitDirectoryService:
    """Who belongs to which unit, on this request's transaction."""
    return UnitDirectoryService(session)


async def get_unit_membership(
    request: Request,
    actor: Annotated[Actor, Depends(get_current_web_actor)],
    directory: Annotated[UnitDirectoryService, Depends(get_unit_directory)],
) -> UnitMembership:
    """The actor's unit tags, resolved once per request.

    Cached on ``request.state`` so the gate on the router and the route body
    that needs the same answer share one query. Units are deliberately not a
    field on :class:`~meobot.domain.identity.models.Actor`: the actor is rebuilt
    from the ``users`` row everywhere, and membership is a lookup beside it.
    """
    cached = getattr(request.state, "unit_membership", None)
    if isinstance(cached, UnitMembership):
        return cached
    membership = await directory.membership_for(actor)
    request.state.unit_membership = membership
    return membership


def require_unit(code: UnitCode) -> Callable[..., Awaitable[UnitMembership]]:
    """A router-level gate: the caller must be tagged into ``code``.

    Mounted at include time (``dependencies=[Depends(require_unit(UnitCode.PR))]``)
    so no route body changes and the existing ``get_current_web_actor`` is still
    in every route's dependency tree. An outsider gets the PR routes' usual
    "not visible" 404, never a 403 that confirms the unit holds something.
    """

    async def _gate(
        membership: Annotated[UnitMembership, Depends(get_unit_membership)],
    ) -> UnitMembership:
        if not membership.has(code):
            raise UnitNotFoundError(NOT_VISIBLE, details={"reason": "unit_not_visible"})
        return membership

    _gate.__name__ = f"require_unit_{code.value.lower()}"
    return _gate


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ActorDep = Annotated[Actor, Depends(get_current_system_actor)]
RequestIdDep = Annotated[uuid.UUID, Depends(get_request_id)]
HealthServiceDep = Annotated[HealthService, Depends(get_health_service)]
ScriptTypeServiceDep = Annotated[ScriptTypeService, Depends(get_script_type_service)]
SheetProfileServiceDep = Annotated[SheetProfileService, Depends(get_sheet_profile_service)]
SheetsClientDep = Annotated[SheetsClient, Depends(get_sheets_client)]
LLMProviderDep = Annotated[LLMProvider, Depends(get_llm_provider)]
ScriptServiceDep = Annotated[ScriptService, Depends(get_script_service)]
ScriptReviewServiceDep = Annotated[ScriptReviewService, Depends(get_script_review_service)]
SheetInspectionServiceDep = Annotated[SheetInspectionService, Depends(get_sheet_inspection_service)]
SheetSyncServiceDep = Annotated[ScriptSyncService, Depends(get_sheet_sync_service)]
InviteServiceDep = Annotated[InviteService, Depends(get_invite_service)]
DriveClientDep = Annotated[DriveClient, Depends(get_drive_client)]
DriveFolderServiceDep = Annotated[DriveFolderService, Depends(get_drive_folder_service)]
SheetTemplateServiceDep = Annotated[SheetTemplateService, Depends(get_sheet_template_service)]
SpreadsheetCreationServiceDep = Annotated[
    SpreadsheetCreationService, Depends(get_spreadsheet_creation_service)
]
ChatMemoryServiceDep = Annotated[ChatMemoryService, Depends(get_chat_memory_service)]
WebAuthServiceDep = Annotated[WebAuthService, Depends(get_web_auth_service)]
#: The authenticated person behind a browser request. Use this on every PR
#: route; ``ActorDep`` above is the milestone-1 stub and grants OWNER to
#: anybody.
CurrentActorDep = Annotated[Actor, Depends(get_current_web_actor)]
#: For OAuth callbacks only - see :func:`get_optional_web_actor`. Every other
#: route uses :data:`CurrentActorDep`.
OptionalActorDep = Annotated[Actor | None, Depends(get_optional_web_actor)]
PrServicesDep = Annotated[PrServices, Depends(get_pr_services)]
#: The session behind :data:`CurrentActorDep` (``None`` under a test override).
CurrentWebSessionDep = Annotated[ResolvedSession | None, Depends(get_current_web_session)]
PasswordServiceDep = Annotated[PasswordService, Depends(get_password_service)]
PasswordResetServiceDep = Annotated[PasswordResetService, Depends(get_password_reset_service)]
AccountServiceDep = Annotated[AccountService, Depends(get_account_service)]
AvatarServiceDep = Annotated[AvatarService, Depends(get_avatar_service)]
UnitDirectoryDep = Annotated[UnitDirectoryService, Depends(get_unit_directory)]
#: The caller's unit tags. Resolved once per request and cached on it.
UnitMembershipDep = Annotated[UnitMembership, Depends(get_unit_membership)]
