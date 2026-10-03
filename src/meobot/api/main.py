"""FastAPI application factory, lifespan and error handling."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from meobot import __version__
from meobot.api.middleware import RequestContextMiddleware
from meobot.api.routers import (
    access,
    conversations,
    drive,
    health,
    invites,
    notifications,
    pr,
    pr_content_work,
    pr_members,
    pr_performance,
    pr_work,
    pr_work_maintenance,
    pr_work_quota,
    pr_work_recurring,
    script_types,
    scripts,
    sheet_profiles,
    system,
    web_auth,
)
from meobot.api.schemas.common import ErrorResponse
from meobot.api.schemas.pr import ErrorBody, ErrorEnvelope
from meobot.application.health_service import HealthService
from meobot.core.config import Settings, get_settings
from meobot.core.context import get_request_id
from meobot.core.errors import (
    AuthorizationError,
    ConfigurationError,
    ConflictError,
    MeoBotError,
    NotFoundError,
    ValidationError,
    WorkflowStateError,
)
from meobot.core.logging import configure_logging, get_logger
from meobot.db.session import Database
from meobot.domain.pr.errors import PrImmutableFieldError
from meobot.integrations.google.factory import build_drive_client, build_sheets_client
from meobot.integrations.llm.factory import build_llm_provider
from meobot.tools.registry import build_default_registry

logger = get_logger(__name__)

#: Domain error -> HTTP status. **Order matters** - the first match wins, so the
#: narrow entries come before the base classes they inherit from.
#:
#: Most of the PR vocabulary lands here by inheritance rather than by being
#: listed: ``PrNotFoundError`` is a ``NotFoundError``, ``PrStaleVersionError`` and
#: ``PrConflictError`` are ``ConflictError``, ``PrPermissionDeniedError``
#: is an ``AuthorizationError``. Step 1E added two entries to close real gaps:
#:
#: * ``WorkflowStateError`` -> **409**. This covers ``PrWorkflowTransitionError``,
#:   ``PrAiReviewRequiredError`` and ``PrApprovalStageMismatchError``, all of
#:   which previously fell through to a bare 400. They are conflicts with the
#:   current state of the record - a client should re-read and reconsider, which
#:   is exactly what 409 tells it to do;
#: * ``PrImmutableFieldError`` -> **409**, ahead of its ``ValidationError`` base.
#:   The request is well-formed; the target simply cannot be changed. 422 would
#:   send somebody looking for a typo in a body that has none.
_STATUS_MAP: tuple[tuple[type[MeoBotError], int], ...] = (
    (PrImmutableFieldError, status.HTTP_409_CONFLICT),
    (NotFoundError, status.HTTP_404_NOT_FOUND),
    (ConflictError, status.HTTP_409_CONFLICT),
    (WorkflowStateError, status.HTTP_409_CONFLICT),
    (ValidationError, status.HTTP_422_UNPROCESSABLE_ENTITY),
    (AuthorizationError, status.HTTP_403_FORBIDDEN),
    (ConfigurationError, status.HTTP_500_INTERNAL_SERVER_ERROR),
)

#: Paths whose failures use the ``{"error": {...}}`` envelope the web client
#: branches on. The milestone-1 routers keep their flat body: changing it would
#: break callers for cosmetic consistency, and both shapes carry the same code,
#: message and details.
_ENVELOPE_PREFIXES = ("/api/pr", "/api/auth", "/api/notifications", "/auth/")


def _http_status_for(error: MeoBotError) -> int:
    for error_type, http_status in _STATUS_MAP:
        if isinstance(error, error_type):
            return http_status
    return status.HTTP_400_BAD_REQUEST


def _web_origins(settings: Settings) -> list[str]:
    """Browser origins allowed to call the API with a session cookie.

    **Only ``WEB_EXTRA_ALLOWED_ORIGINS``**, and empty by default - in which case
    no CORS middleware is installed at all.

    Step 1E derived this from ``WEB_BASE_URL`` too, which was wrong in a quiet
    way: in the supported topology Next.js proxies ``/api``, so the browser only
    ever sees one origin and CORS plays no part. Deriving an allowed origin from
    the panel's own address therefore opened a credentialed cross-origin path
    that nothing used and that widened what an attacker-controlled page could
    attempt. Cross-origin browser access is now something an operator asks for by
    name.

    Never contains ``*``. The session is a cookie, so a wildcard origin with
    credentials is a CSRF gift - and browsers reject the combination anyway.
    """
    candidates = settings.web_extra_allowed_origins.split(",")
    return [origin for origin in (item.strip().rstrip("/") for item in candidates) if origin]


#: Routers that a browser is meant to reach. The Step 1E web surface, the service
#: banner and the health probes - nothing else.
def _include_web_facing_routers(app: FastAPI) -> None:
    """Mount the browser-facing surface.

    Health stays unauthenticated on purpose: ``docker compose`` and
    ``scripts/nas.sh`` poll it, and it reports component up/down and a timestamp -
    no configuration, no versions of dependencies, no user data.
    """
    app.include_router(health.router)
    app.include_router(web_auth.router)
    app.include_router(pr.router)
    # M1. Its own router because the Work Ledger is a separate additive module;
    # same prefix family, same dependencies, no route in ``pr.router`` changes.
    # M2, and mounted **before** M1's work router on purpose. They share the
    # ``/api/pr/work`` prefix, and M1 owns ``GET /api/pr/work/{work_item_id}``:
    # FastAPI matches in registration order, so the literal ``/periods``,
    # ``/plans`` and ``/eligibility`` paths have to be declared first or every
    # one of them would be parsed as a work-item UUID and answered with a 422.
    # Neither router's own routes change; only which is asked first.
    app.include_router(pr_work_quota.router)
    # M3, and mounted before M1's work router for the same reason M2's is: it
    # owns the literal ``/api/pr/work/content`` prefix, and M1 owns
    # ``GET /api/pr/work/{work_item_id}``. FastAPI matches in registration
    # order, so "content" would otherwise be parsed as a work-item UUID.
    app.include_router(pr_content_work.router)
    # M4B, and mounted before M1's work router for the same reason M2's and M3's
    # are: it owns the literal ``/api/pr/work/recurring`` prefix, and M1 owns
    # ``GET /api/pr/work/{work_item_id}``. Declared later, "recurring" would be
    # parsed as a work-item UUID and answered with a 422.
    app.include_router(pr_work_recurring.router)
    # Work maintenance. ``/api/pr/work/maintenance``, mounted before M1's work
    # router for the same reason as the three above.
    app.include_router(pr_work_maintenance.router)
    app.include_router(pr_work.router)
    # M6. Its own ``/api/pr/performance`` prefix, so ordering against M1's
    # ``/api/pr/work/{work_item_id}`` does not arise - unlike M2's and M3's,
    # which had to be mounted first because they extend that prefix.
    app.include_router(pr_performance.router)
    # Membership phase 1. ``/api/pr/members`` and ``/api/pr/roles``: reads of
    # its own, writes delegated to ``UserService`` - the Telegram commands'
    # service - so the two clients cannot disagree about who may do what.
    app.include_router(pr_members.router)
    # Step 1F.2.3d. Browser-facing and session-authenticated like the two above.
    # Not under the PR prefix: a notification is not a PR object, and the routes
    # know nothing about content beyond an opaque ``target_kind``.
    app.include_router(notifications.router)


def _include_internal_routers(app: FastAPI) -> None:
    """Mount the milestone-1 ``/api/v1/*`` surface. **Off by default.**

    Every router here predates authentication. Between them they expose 24 write
    endpoints attributed to a synthetic ``OWNER`` and 26 reads with no actor at
    all - including ``PATCH /api/v1/users/{id}/role``, which is a self-promotion
    to OWNER for anybody who can reach the port, and ``GET /api/v1/invites``,
    which lists live invite codes.

    Fixing all 51 individually would mean rewriting five routers' authorization,
    which is a larger change than a hardening pass should make and is not what
    they are for: nothing in the bot, the workers or ``scripts/nas.sh`` calls
    them. So they are excluded from the served surface unless somebody asks for
    them, and ``get_current_system_actor`` refuses to produce an actor when they
    are not mounted - so a router accidentally added to the web-facing list above
    fails closed instead of granting OWNER.
    """
    app.include_router(system.router)
    app.include_router(script_types.router)
    app.include_router(sheet_profiles.router)
    app.include_router(scripts.router)
    app.include_router(invites.router)
    app.include_router(conversations.router)
    app.include_router(drive.router)
    app.include_router(access.router)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build shared collaborators on startup, dispose them on shutdown."""
    settings: Settings = app.state.settings
    database = Database(settings)
    health_service = HealthService(database, settings)
    sheets_client = build_sheets_client(settings)
    drive_client = build_drive_client(settings)
    llm_provider = build_llm_provider(settings)

    app.state.database = database
    app.state.health_service = health_service
    app.state.sheets_client = sheets_client
    app.state.drive_client = drive_client
    app.state.llm_provider = llm_provider
    app.state.tool_registry = build_default_registry(
        health_service=health_service,
        sheets=sheets_client,
        llm=llm_provider,
        drive=drive_client,
    )

    logger.info("api_started", extra={"app_env": settings.app_env, "version": __version__})
    try:
        yield
    finally:
        for client in (sheets_client, drive_client, llm_provider):
            closer = getattr(client, "aclose", None)
            if callable(closer):
                await closer()
        await database.dispose()
        logger.info("api_stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application.

    Args:
        settings: Override for tests. Defaults to the cached process settings.
    """
    resolved = settings or get_settings()
    configure_logging(
        service="api",
        level=resolved.log_level,
        log_format=resolved.log_format,
    )

    # Interactive docs publish a complete map of the surface, including whatever
    # internal routers happen to be mounted. Harmless in development, free
    # reconnaissance in production - so production has to ask twice.
    docs_enabled = resolved.api_docs_enabled and not resolved.is_production
    if resolved.api_docs_enabled and resolved.is_production:
        logger.warning("api_docs_disabled_in_production")
    app = FastAPI(
        title=f"{resolved.app_name} internal API",
        version=__version__,
        description=(
            "Internal operations API for MeoBot. Telegram is the primary "
            "interface; this API exists for health checks, development and "
            "future OAuth callbacks. It is bound to 127.0.0.1 on the NAS."
        ),
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    app.state.settings = resolved
    app.add_middleware(RequestContextMiddleware)

    origins = _web_origins(resolved)
    if origins:
        # Credentialed CORS, so an explicit origin list is mandatory - the spec
        # forbids ``*`` with credentials and browsers enforce it. Methods and
        # headers are listed rather than wildcarded for the same reason: this is
        # the door the PR panel comes through, and nothing else needs it open.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Content-Type"],
        )

    @app.exception_handler(MeoBotError)
    async def meobot_error_handler(request: Request, exc: MeoBotError) -> JSONResponse:
        """Translate domain errors into a uniform error body."""
        http_status = _http_status_for(exc)
        if http_status >= status.HTTP_500_INTERNAL_SERVER_ERROR:
            logger.error("unhandled_domain_error", extra={"error_code": exc.code}, exc_info=exc)
        else:
            logger.info("domain_error", extra={"error_code": exc.code})
        if request.url.path.startswith(_ENVELOPE_PREFIXES):
            body = ErrorEnvelope(
                error=ErrorBody(code=exc.code, message=exc.message, details=exc.details)
            ).model_dump(mode="json")
        else:
            body = ErrorResponse(
                code=exc.code,
                message=exc.message,
                request_id=get_request_id(),
                details=exc.details,
            ).model_dump(mode="json")
        return JSONResponse(status_code=http_status, content=body)

    @app.get("/", tags=["root"], summary="Service banner")
    async def root() -> dict[str, str]:
        """Identify the service and point at the docs."""
        return {
            "service": resolved.app_name,
            "version": __version__,
            "environment": resolved.app_env,
            "docs": "/docs" if docs_enabled else "disabled",
            "interface": "Telegram is the primary interface; this API is internal.",
        }

    _include_web_facing_routers(app)
    if resolved.api_internal_routers_enabled:
        # Logged at warning level, once, with the count. Somebody reading startup
        # output should not have to know this setting exists to notice that 51
        # weakly-authenticated routes are being served.
        logger.warning(
            "internal_routers_enabled",
            extra={"detail": "api/v1 routers are served; do not expose this port"},
        )
        _include_internal_routers(app)
    return app


#: ASGI entry point used by uvicorn (``meobot.api.main:app``).
app = create_app()
