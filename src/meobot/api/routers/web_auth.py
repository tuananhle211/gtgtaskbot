"""Web session endpoints: redeem a magic link, read it back, sign out.

Step 1E. Three routes, and none of them decides anything - they set and clear a
cookie around :class:`~meobot.application.web_auth_service.WebAuthService`.

There is deliberately **no route that issues a login link.** Issuing one is what
proves identity, and the proof is Telegram delivering a private message to an
account already tied to a ``users`` row. An HTTP endpoint that minted links would
be an endpoint that hands a credential to whoever asks for it. So the only way in
is ``/web`` in the bot.

Cookie flags, and why each one
------------------------------

``HttpOnly`` - script cannot read it, so an XSS bug in the panel cannot exfiltrate
the session. ``Secure`` - it never travels in clear (settable off only for
``http://localhost``, where browsers reject ``Secure`` outright). ``SameSite=Strict``
- **this is the CSRF defence.** The browser will not attach the cookie to a
request another site caused, so a form on an attacker's page posting to
``/api/pr/contents/…/reviews`` arrives with no session and is refused with 401.
That is why there is no CSRF token here: with ``Strict`` there is no
cross-site request carrying credentials for a token to protect.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import RedirectResponse

from meobot.api.deps import (
    CurrentActorDep,
    PrServicesDep,
    RequestIdDep,
    SettingsDep,
    WebAuthServiceDep,
)
from meobot.api.schemas.pr import ActorResponse
from meobot.application.web_auth_service import LOGIN_TOKEN_PARAM, SESSION_COOKIE
from meobot.core.errors import ValidationError
from meobot.domain.audit.models import AuditAction, AuditResult

router = APIRouter(tags=["web-auth"])

#: Where a successful login lands. The panel's own root, so the person ends up
#: looking at work rather than at a JSON body.
_AFTER_LOGIN = "/pr"

#: Where a failed login lands, with a reason the page can render. No detail about
#: *why* the token failed - see ``WebAuthService.redeem_login_token``.
_LOGIN_FAILED = "/auth/failed"


def _set_session_cookie(response: Response, *, token: str, max_age: int, secure: bool) -> None:
    """Attach the session cookie. One place, so no route can forget a flag."""
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
    )


@router.get(
    "/auth/login",
    summary="Redeem a Telegram login link",
    status_code=status.HTTP_303_SEE_OTHER,
)
async def redeem_login(
    request: Request,
    auth: WebAuthServiceDep,
    settings: SettingsDep,
    token: str = Query(alias=LOGIN_TOKEN_PARAM, min_length=1),
) -> Response:
    """Turn the link the bot sent into a session cookie, then redirect.

    A redirect rather than a JSON body because a person opens this in a browser
    bar. It also gets the token out of the address bar promptly: a URL with a
    credential in it ends up in history, and in the ``Referer`` of the next
    request the page makes.

    Failure redirects too, with no detail. A 422 rendered as raw JSON tells
    somebody who clicked an old link that the software is broken, when the honest
    message is "ask the bot for a new one".
    """
    try:
        issued = await auth.redeem_login_token(
            token=token,
            user_agent=request.headers.get("user-agent"),
            ip=request.client.host if request.client else None,
        )
    except ValidationError:
        return RedirectResponse(_LOGIN_FAILED, status_code=status.HTTP_303_SEE_OTHER)

    response = RedirectResponse(_AFTER_LOGIN, status_code=status.HTTP_303_SEE_OTHER)
    _set_session_cookie(
        response,
        token=issued.token,
        max_age=settings.web_session_ttl_seconds,
        secure=settings.web_cookie_secure,
    )
    return response


@router.get(
    "/api/auth/session",
    response_model=ActorResponse,
    summary="Who am I, and what may I do",
    responses={401: {"description": "No usable session."}},
)
async def current_session(
    actor: CurrentActorDep,
    services: PrServicesDep,
) -> ActorResponse:
    """The signed-in person and their current PR capabilities.

    The frontend calls this on load and uses ``capabilities`` to decide which
    controls to render. That is presentation only: every write is re-checked in
    the services, so a caller who forges this response gains nothing but buttons
    that fail.

    Capabilities are recomputed here rather than stored in the session, so a
    grant made a minute ago shows up on the next page load.
    """
    return ActorResponse.from_actor(
        actor, await services.capabilities.capabilities_for_actor(actor)
    )


@router.post(
    "/api/auth/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign this browser out",
)
async def logout(
    request: Request,
    response: Response,
    auth: WebAuthServiceDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> Response:
    """Revoke the session and clear the cookie.

    Deliberately **not** guarded by :data:`CurrentActorDep`: logging out with an
    already-dead session should clear the cookie and succeed, not 401. Signing
    out is never the operation to make harder.

    The cookie is deleted whether or not a live session was found, because the
    outcome the person asked for is "this browser is signed out".
    """
    token = request.cookies.get(SESSION_COOKIE)
    # Resolve *before* revoking. Afterwards the session is dead and
    # ``resolve_session`` returns None, so the audit line would have no subject.
    actor = await auth.resolve_session(token=token)
    if await auth.revoke_session(token=token) and actor is not None:
        await services.audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.WEB_SESSION_REVOKED,
            result=AuditResult.SUCCESS,
            entity_type="web_session",
        )
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


__all__: list[str] = ["router"]
