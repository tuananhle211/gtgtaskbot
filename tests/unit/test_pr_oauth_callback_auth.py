"""The OAuth callback authenticates from its state, not from a session cookie.

A production bug, and a narrow one worth its own file. Both connector callbacks
declared ``actor: CurrentActorDep``, so FastAPI resolved the session **before**
the route body ran. A browser returning from Meta's consent screen therefore got:

    401  "Bạn cần đăng nhập lại."

after consenting successfully - and the connection was never established.

Why the session was the wrong thing to depend on
-------------------------------------------------

A callback is not a call. The browser arrives by **redirect from another site**,
so whether MeoBot's session cookie rides along is a question about ``SameSite``
policy and the browser's mood, not about whether the flow is legitimate. Making
a security-critical step depend on that means it works in one browser and fails
in another, and the temptation when it fails is to widen the cookie - which
would trade a working callback for a CSRF surface on every other route.

The flow already carried its identity. The OAuth state is written by MeoBot at
authorization time and binds the user, the channel, the provider and an expiry;
it is single-use and only its hash is stored. That is the mechanism designed to
survive an external round trip, and the fix is to read identity from it.

What this file pins
-------------------

That the fix did not become a hole. A callback with **no session** must succeed;
a callback with the **wrong session** must not; and none of unknown, expired,
reused or wrong-session may be distinguishable from the others, because telling
them apart is exactly what a prober wants. The last of those is the one a
careless fix gets wrong - "belongs to another user" is a helpful message and a
disclosure.
"""

from __future__ import annotations

# ruff: noqa: F811 - ``world`` is a fixture reused across the connector suites.
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select

from meobot.core.time import utcnow
from meobot.db.models.pr_channel_connection import (
    PrChannelConnection,
    PrChannelOAuthState,
)
from meobot.domain.identity.models import Role
from meobot.domain.pr.channel_metrics import PrChannelPlatform
from meobot.domain.pr.errors import PrPermissionDeniedError, PrValidationError
from tests.unit.test_pr_channel_metrics import channel as make_channel
from tests.unit.test_pr_meta_connector import FakeGraph, connector_settings
from tests.unit.test_pr_production_lifecycle import (  # noqa: F401 - fixture
    World,
    world,
)
from tests.unit.test_pr_youtube_connector import FakeProvider

#: Every refusal a callback can produce reads the same to the caller. Listed
#: once so the "indistinguishable" test cannot drift from the implementation.
GENERIC_FAILURE = "không hợp lệ hoặc đã hết hạn"


@pytest.fixture(autouse=True)
def _configured(world: World) -> None:
    settings = connector_settings()
    world.services.channel_connections._settings = settings
    world.services.channel_sync._settings = settings
    world.services.settings = settings


async def start_flow(
    world: World,
    *,
    provider: PrChannelPlatform,
    actor_user: Any = None,
    platform_code: str | None = None,
) -> tuple[Any, str]:
    """Begin an authorization the way the panel does, and hand back its state."""
    code = platform_code or provider.value
    row = await make_channel(world, platform_code=code, name=f"Kênh {code}")
    start = await world.services.channel_connections.start_authorization(
        actor=world.actor(actor_user or world.owner),
        channel_id=row.id,
        provider=provider,
    )
    return row, start.authorization_url.split("state=")[1]


def client_for(provider: PrChannelPlatform) -> Any:
    return FakeProvider() if provider is PrChannelPlatform.YOUTUBE else FakeGraph().facebook()


# ===========================================================================
# The bug itself
# ===========================================================================


@pytest.mark.parametrize("provider", [PrChannelPlatform.YOUTUBE, PrChannelPlatform.FACEBOOK])
async def test_a_valid_callback_succeeds_with_no_session_at_all(
    world: World, provider: PrChannelPlatform
) -> None:
    """The regression. This is the exact production failure, both providers.

    ``session_actor=None`` is what an OAuth redirect looks like when the browser
    withholds the cookie. Before the fix this could not even reach the service.
    """
    row, token = await start_flow(world, provider=provider)

    outcome = await world.services.channel_connections.finish_authorization(
        request_id=world.request_id,
        state_token=token,
        code="auth-code",
        session_actor=None,
        provider_client=client_for(provider),
    )

    assert outcome.connection.channel_id == row.id
    assert outcome.connection.provider is provider
    # Attribution survives: the connection names the person who *started* the
    # flow, taken from the state, not "nobody".
    assert outcome.connection.connected_by_user_id == world.owner.id


@pytest.mark.parametrize("provider", [PrChannelPlatform.YOUTUBE, PrChannelPlatform.FACEBOOK])
async def test_a_valid_callback_succeeds_with_the_matching_session(
    world: World, provider: PrChannelPlatform
) -> None:
    """The other half: a cookie that *did* survive the redirect is fine too.

    Both paths must work, because which one happens is decided by the browser.
    """
    row, token = await start_flow(world, provider=provider)

    outcome = await world.services.channel_connections.finish_authorization(
        request_id=world.request_id,
        state_token=token,
        code="auth-code",
        session_actor=world.actor(world.owner),
        provider_client=client_for(provider),
    )
    assert outcome.connection.channel_id == row.id
    assert outcome.connection.connected_by_user_id == world.owner.id


async def test_the_callback_route_does_not_require_a_session(world: World) -> None:
    """Over HTTP, with no cookie, against both callback paths.

    The service-level tests above prove the logic; this proves the **wiring**,
    which is where the bug actually was. A dependency resolved before the route
    body is invisible to every service test.
    """
    app = world.client.app
    from meobot.api.deps import get_current_web_actor

    # Simulate a browser with no MeoBot session: the strict dependency fails
    # closed exactly as it does in production.
    original = app.dependency_overrides.pop(get_current_web_actor, None)
    try:
        for path in (
            "/api/pr/channels/connections/meta/callback",
            "/api/pr/channels/connections/youtube/callback",
        ):
            response = world.client.get(
                f"{path}?state=not-a-real-state&code=abc", follow_redirects=False
            )
            # The state is bogus, so this redirects to the failure page - but it
            # is a **redirect**, not a 401. Before the fix it was a 401 and the
            # route body never ran.
            assert response.status_code == 303, f"{path}: {response.status_code}"
            assert "connection=failed" in response.headers["location"]
    finally:
        if original is not None:
            app.dependency_overrides[get_current_web_actor] = original


# ===========================================================================
# The fix did not become a hole
# ===========================================================================


async def test_a_callback_carrying_someone_elses_session_is_refused(
    world: World,
) -> None:
    """Defence in depth, and it must not be chatty.

    A browser that sent a session belonging to a different person is refused -
    but with the generic failure, because "this belongs to somebody else"
    confirms both that the state exists and that another user owns it.
    """
    _, token = await start_flow(world, provider=PrChannelPlatform.FACEBOOK)

    with pytest.raises(PrValidationError) as failure:
        await world.services.channel_connections.finish_authorization(
            request_id=world.request_id,
            state_token=token,
            code="auth-code",
            session_actor=world.actor(world.head),
            provider_client=FakeGraph().facebook(),
        )
    assert failure.value.details["reason"] == "state_session_mismatch"
    assert GENERIC_FAILURE in failure.value.message


async def test_every_bad_state_looks_identical_at_the_callback(world: World) -> None:
    """Unknown, expired, reused and wrong-session are one page to a stranger.

    Asserted at the **callback**, which is the surface that matters: it is
    unauthenticated, so anything it distinguishes is something a prober can
    enumerate. All four land on the same ``connection=failed`` redirect with no
    message at all.

    The service *does* keep distinct wording for "expired" and "already used" -
    those are shown on the authenticated panel path, where they tell a person
    what to do and disclose nothing to anybody else. That asymmetry is
    deliberate; see ``_redeem_state``.
    """
    app = world.client.app
    from meobot.api.deps import get_current_web_actor

    _, live = await start_flow(world, provider=PrChannelPlatform.FACEBOOK)
    _, expiring = await start_flow(
        world, provider=PrChannelPlatform.FACEBOOK, platform_code="FACEBOOK"
    )
    states = (await world.session.execute(select(PrChannelOAuthState))).scalars().all()
    states[-1].expires_at = utcnow() - timedelta(minutes=1)
    await world.session.flush()

    await world.services.channel_connections.finish_authorization(
        request_id=world.request_id,
        state_token=live,
        code="auth-code",
        session_actor=None,
        provider_client=FakeGraph().facebook(),
    )

    original = app.dependency_overrides.pop(get_current_web_actor, None)
    try:
        seen = set()
        for token in ("never-issued", expiring, live):
            response = world.client.get(
                f"/api/pr/channels/connections/meta/callback?state={token}&code=abc",
                follow_redirects=False,
            )
            assert response.status_code == 303
            seen.add(response.headers["location"])
        assert len(seen) == 1, seen
        assert "connection=failed" in seen.pop()
    finally:
        if original is not None:
            app.dependency_overrides[get_current_web_actor] = original


async def test_the_disclosing_case_uses_the_unhelpful_wording(world: World) -> None:
    """ "Somebody else started this" is the one message that must not be sent.

    Even on the authenticated path: a signed-in person presenting a state they
    do not own would otherwise learn that the state exists and belongs to
    another user.
    """
    _, token = await start_flow(world, provider=PrChannelPlatform.FACEBOOK)
    with pytest.raises(PrValidationError) as mismatch:
        await world.services.channel_connections.finish_authorization(
            request_id=world.request_id,
            state_token=token,
            code="auth-code",
            session_actor=world.actor(world.head),
            provider_client=FakeGraph().facebook(),
        )
    with pytest.raises(PrValidationError) as unknown:
        await world.services.channel_connections.finish_authorization(
            request_id=world.request_id,
            state_token="never-issued",
            code="auth-code",
            session_actor=None,
            provider_client=FakeGraph().facebook(),
        )
    assert mismatch.value.message == unknown.value.message


async def test_a_replayed_state_cannot_establish_a_second_connection(
    world: World,
) -> None:
    """Single use survives the fix, and is still enforced *before* the exchange."""
    row, token = await start_flow(world, provider=PrChannelPlatform.FACEBOOK)
    await world.services.channel_connections.finish_authorization(
        request_id=world.request_id,
        state_token=token,
        code="auth-code",
        session_actor=None,
        provider_client=FakeGraph().facebook(),
    )
    with pytest.raises(PrValidationError) as failure:
        await world.services.channel_connections.finish_authorization(
            request_id=world.request_id,
            state_token=token,
            code="auth-code",
            session_actor=None,
            provider_client=FakeGraph().facebook(),
        )
    assert failure.value.details["reason"] == "state_consumed"

    connections = (
        (
            await world.session.execute(
                select(PrChannelConnection).where(PrChannelConnection.channel_id == row.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(connections) == 1


async def test_nothing_in_the_request_can_choose_the_user_channel_or_provider(
    world: World,
) -> None:
    """The whole point of resolving from the state.

    ``finish_authorization`` takes a state and a code and **nothing else** that
    could name a subject. There is no ``user_id``, ``channel_id`` or ``provider``
    parameter to inject, and the callback route passes none - so a crafted URL
    has nothing to aim at.
    """
    import inspect

    from meobot.application.pr_channel_connection_service import (
        PrChannelConnectionService,
    )

    signature = inspect.signature(PrChannelConnectionService.finish_authorization)
    assert set(signature.parameters) == {
        "self",
        "request_id",
        "state_token",
        "code",
        "session_actor",
        "provider_client",
    }

    # And the two callback routes pass only what the browser could have carried.
    from pathlib import Path

    import meobot.api.routers.pr as router_module

    source = Path(router_module.__file__).read_text("utf-8")
    body = source[source.index("async def _finish_oauth_callback") :]
    body = body[: body.index("\n@router")]
    # Scoped to the call that establishes the connection. The function does
    # mention ``channel_id`` afterwards - building the *outgoing* redirect from
    # the outcome - and that is the state's answer travelling back to the
    # browser, not an input.
    call = body[body.index("finish_authorization(") :]
    call = call[: call.index(")")]
    for forbidden in ("channel_id", "provider", "user_id"):
        assert forbidden not in call, forbidden


async def test_a_deactivated_initiator_cannot_finish_their_own_flow(
    world: World,
) -> None:
    """The window between authorizing and returning is where somebody gets suspended.

    Completing the bind then would leave a live platform credential attributed
    to an account no longer permitted to hold one - and the callback is
    unauthenticated by design, so nothing else would have caught it.
    """
    _, token = await start_flow(world, provider=PrChannelPlatform.FACEBOOK)

    world.owner.active = False
    await world.session.flush()

    with pytest.raises(PrValidationError) as failure:
        await world.services.channel_connections.finish_authorization(
            request_id=world.request_id,
            state_token=token,
            code="auth-code",
            session_actor=None,
            provider_client=FakeGraph().facebook(),
        )
    assert failure.value.details["reason"] == "state_user_unavailable"
    assert GENERIC_FAILURE in failure.value.message


async def test_the_capability_is_still_checked_against_the_state_bound_user(
    world: World,
) -> None:
    """Authorization did not move - only authentication did.

    The state names who started the flow; the capability check is made against
    **their** role. Somebody who has lost channel-management authority since
    starting cannot finish, and the check is not skipped merely because the
    callback carries no session.
    """
    row = await make_channel(world, platform_code="FACEBOOK", name="Kênh FB")
    start = await world.services.channel_connections.start_authorization(
        actor=world.actor(world.owner),
        channel_id=row.id,
        provider=PrChannelPlatform.FACEBOOK,
    )
    token = start.authorization_url.split("state=")[1]

    # Demote the initiator after they left for the consent screen.
    world.owner.role = Role.EMPLOYEE
    await world.session.flush()

    with pytest.raises(PrPermissionDeniedError):
        await world.services.channel_connections.finish_authorization(
            request_id=world.request_id,
            state_token=token,
            code="auth-code",
            session_actor=None,
            provider_client=FakeGraph().facebook(),
        )


async def test_the_state_is_consumed_before_any_provider_call(world: World) -> None:
    """Anti-replay ordering survived the change.

    If the exchange happened first, two callbacks racing on one state would both
    reach the provider - two authorization codes spent, and a race on the
    connection row. The state is marked used before anything leaves the process.
    """
    _, token = await start_flow(world, provider=PrChannelPlatform.FACEBOOK)

    class Exploding(FakeGraph):
        pass

    graph = Exploding(status=503)
    with pytest.raises(Exception):  # noqa: B017 - any provider failure will do
        await world.services.channel_connections.finish_authorization(
            request_id=world.request_id,
            state_token=token,
            code="auth-code",
            session_actor=None,
            provider_client=graph.facebook(),
        )

    state = (await world.session.execute(select(PrChannelOAuthState))).scalars().all()[-1]
    assert state.consumed_at is not None, "consumed even though the provider failed"


async def test_no_secret_reaches_the_callback_response(world: World) -> None:
    """Containment is unchanged: the redirect carries a status token and an id."""
    row, token = await start_flow(world, provider=PrChannelPlatform.FACEBOOK)
    outcome = await world.services.channel_connections.finish_authorization(
        request_id=world.request_id,
        state_token=token,
        code="auth-code",
        session_actor=None,
        provider_client=FakeGraph(
            pages=[
                {
                    "id": "111111111111111",
                    "name": "Apex Media",
                    "access_token": "EAApage-a-token",
                }
            ]
        ).facebook(),
    )
    stored = outcome.connection.encrypted_credential
    assert stored is not None
    assert "EAApage-a-token" not in stored

    world.act_as(world.owner)
    response = world.client.get(f"/api/pr/channels/{row.id}/connection")
    assert response.status_code == 200
    for secret in ("EAApage-a-token", "app-secret", "encrypted_credential"):
        assert secret not in response.text
