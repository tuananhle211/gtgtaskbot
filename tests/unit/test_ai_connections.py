""" "Kết nối AI" (0064).

* the router asks a purpose's chain in order and falls back on any LLMError;
  a purpose without a chain uses the default one; with nothing configured the
  environment provider answers, and it closes every chain unless it is fake;
* the screen is the OWNER's; keys are encrypted, never sent back (a hint
  only), kept when an edit leaves them blank;
* routes are validated; deleting a connection takes it out of every route;
* the connection test records how it went;
* the loader keeps active connections only, in the stored order.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from meobot.api.routers.ai import get_ai_service
from meobot.application.ai.connection_service import AiConnectionService
from meobot.application.ai.routing import key_aad, load_route_table
from meobot.application.audit_service import AuditService
from meobot.core.errors import LLMError
from meobot.core.secrets import build_secret_box, generate_key
from meobot.db.models.ai_connection import AiConnection
from meobot.db.models.audit_log import AuditLog
from meobot.domain.ai.models import AiPurpose, purpose_of_task
from meobot.domain.identity.models import Role
from meobot.integrations.llm.base import StructuredRequest
from meobot.integrations.llm.diagnostics import ErrorCategory
from meobot.integrations.llm.routed import RoutedLLMProvider, RouteTable
from tests.unit.pr_world import World
from tests.unit.test_order_commands import person
from tests.unit.test_units_gating import error_reason

pytestmark = pytest.mark.asyncio

KEY = "sk-test-abcdefgh1234"


class Stub:
    """A provider that answers with its own name, or fails."""

    def __init__(self, name: str, *, fails: bool = False) -> None:
        self._name = name
        self._fails = fails
        self.calls: list[str] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def model(self) -> str:
        return f"{self._name}-model"

    async def complete_structured(self, request: StructuredRequest) -> str:
        self.calls.append(request.task)
        if self._fails:
            raise LLMError("hết credit", details={"category": ErrorCategory.RATE_LIMIT})
        return self._name


def ask(task: str) -> StructuredRequest:
    return StructuredRequest(task=task, system_prompt="s", user_prompt="u")


def routed(table: RouteTable, fallback: Any, **kwargs: Any) -> RoutedLLMProvider:
    async def loader() -> RouteTable:
        return table

    return RoutedLLMProvider(fallback=fallback, loader=loader, **kwargs)


# --- the router ------------------------------------------------------------------


async def test_01_nothing_configured_is_the_environment_provider() -> None:
    env = Stub("fake")
    provider = routed(RouteTable(), env)
    assert await provider.complete_structured(ask("order_script_review")) == "fake"
    assert provider.name == "fake"


async def test_02_a_purpose_uses_its_chain_else_the_default_one() -> None:
    script, default = Stub("deepseek"), Stub("gemini")
    provider = routed(
        RouteTable(
            chains={AiPurpose.ORDER_SCRIPT: (script,), AiPurpose.DEFAULT: (default,)}  # type: ignore[dict-item]
        ),
        Stub("fake"),
    )
    assert await provider.complete_structured(ask("order_script_review")) == "deepseek"
    assert await provider.complete_structured(ask("sheet_mapping")) == "gemini"
    assert purpose_of_task("pr_full_review") is AiPurpose.PR_REVIEW
    assert purpose_of_task("anything_else") is AiPurpose.DEFAULT


async def test_03_a_failing_ai_hands_over_to_the_next() -> None:
    broke, backup = Stub("openai", fails=True), Stub("deepseek")
    provider = routed(RouteTable(chains={AiPurpose.DEFAULT: (broke, backup)}), Stub("fake"))  # type: ignore[dict-item]
    assert await provider.complete_structured(ask("x")) == "deepseek"
    assert broke.calls == ["x"] and backup.calls == ["x"]
    # All of them failing: the last failure is raised.
    provider = routed(
        RouteTable(chains={AiPurpose.DEFAULT: (Stub("a", fails=True),)}),  # type: ignore[dict-item]
        Stub("fake"),
    )
    with pytest.raises(LLMError):
        await provider.complete_structured(ask("x"))


async def test_04_the_environment_closes_every_chain_unless_it_is_fake() -> None:
    first = Stub("gemini", fails=True)
    env = Stub("openai")
    provider = routed(RouteTable(chains={AiPurpose.DEFAULT: (first,)}), env)  # type: ignore[dict-item]
    assert await provider.complete_structured(ask("x")) == "openai"
    fake = Stub("fake")
    provider = routed(RouteTable(chains={AiPurpose.DEFAULT: (Stub("g", fails=True),)}), fake)  # type: ignore[dict-item]
    with pytest.raises(LLMError):
        await provider.complete_structured(ask("x"))
    assert fake.calls == []


async def test_05_the_table_is_cached_refreshed_and_kept_when_the_loader_fails() -> None:
    now = [0.0]
    loads = [0]
    tables = [RouteTable(chains={AiPurpose.DEFAULT: (Stub("one"),)})]  # type: ignore[dict-item]

    async def loader() -> RouteTable:
        loads[0] += 1
        if not tables:
            raise RuntimeError("database down")
        return tables[0]

    provider = RoutedLLMProvider(
        fallback=Stub("fake"),  # type: ignore[arg-type]
        loader=loader,
        ttl_seconds=10,
        clock=lambda: now[0],
    )
    assert await provider.complete_structured(ask("x")) == "one"
    assert await provider.complete_structured(ask("x")) == "one"
    assert loads[0] == 1
    tables[0] = RouteTable(chains={AiPurpose.DEFAULT: (Stub("two"),)})  # type: ignore[dict-item]
    provider.refresh()
    assert await provider.complete_structured(ask("x")) == "two"
    tables.clear()
    now[0] = 100.0
    assert await provider.complete_structured(ask("x")) == "two"


# --- the screen ----------------------------------------------------------------------


class FakeFactory:
    def __init__(self) -> None:
        self.fails = False
        self.seen: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> Any:
        self.seen.append(kwargs)
        stub = Stub("test", fails=self.fails)
        return stub


def screen(world: World, *, secret: bool = True) -> FakeFactory:
    settings = world.settings.model_copy(
        update={
            "pr_secret_encryption_key": SecretStr(generate_key()) if secret else None,
            "pr_secret_encryption_key_file": None,
        }
    )
    factory = FakeFactory()
    world.client.app.dependency_overrides[get_ai_service] = lambda: AiConnectionService(  # type: ignore[attr-defined]
        world.session, AuditService(world.session), settings, provider_factory=factory
    )
    world.ai_settings = settings  # type: ignore[attr-defined]
    return factory


def create(world: World, **body: Any) -> Any:
    return world.client.post(
        "/api/ai/connections",
        json={
            "name": "DeepSeek chính",
            "kind": "DEEPSEEK",
            "model": "deepseek-chat",
            "api_key": KEY,
            **body,
        },
    )


async def test_10_only_the_owner_manages_ai(world: World) -> None:
    screen(world)
    admin = await person(world, "Quản trị", Role.ADMIN)
    for refused_for in (admin, world.member):
        world.act_as(refused_for)
        for call in (
            lambda: world.client.get("/api/ai"),
            lambda: create(world),
            lambda: world.client.put("/api/ai/routes", json={"routes": {}}),
        ):
            refused = call()
            assert refused.status_code == 403, refused.json()
            assert error_reason(refused.json()) == "ai_forbidden"


async def test_11_a_key_is_stored_encrypted_and_never_sent_back(world: World) -> None:
    screen(world)
    world.act_as(world.owner)
    made = create(world)
    assert made.status_code == 201, made.json()
    body = made.json()
    assert KEY not in made.text
    assert body["api_key_hint"] == "••••1234"
    assert body["endpoint"] == "https://api.deepseek.com" and body["base_url"] is None
    row = await world.session.get(AiConnection, uuid.UUID(body["id"]))
    assert row is not None and KEY not in row.api_key_encrypted
    box = build_secret_box(world.ai_settings)  # type: ignore[attr-defined]
    assert box.decrypt(row.api_key_encrypted, aad=key_aad(row.id)) == KEY
    overview = world.client.get("/api/ai")
    assert overview.status_code == 200 and KEY not in overview.text
    assert overview.json()["secret_ready"] is True
    assert [item["purpose"] for item in overview.json()["routes"]] == [p.value for p in AiPurpose]
    # A blank key on an edit keeps the stored one.
    edited = world.client.patch(
        f"/api/ai/connections/{row.id}", json={"api_key": " ", "model": "deepseek-reasoner"}
    )
    assert edited.status_code == 200 and edited.json()["api_key_hint"] == "••••1234"
    await world.session.refresh(row)
    assert box.decrypt(row.api_key_encrypted, aad=key_aad(row.id)) == KEY
    # The audit log never holds the key.
    logs = (await world.session.scalars(select(AuditLog).where(AuditLog.action.like("ai.%")))).all()
    assert [log.action for log in logs] == ["ai.connection.created", "ai.connection.updated"]
    assert all(KEY not in str(log.before_data) + str(log.after_data) for log in logs)


async def test_12_what_a_connection_needs(world: World) -> None:
    screen(world)
    world.act_as(world.owner)
    for body, reason in (
        ({"api_key": ""}, "ai_key_missing"),
        ({"model": " "}, "ai_model_missing"),
        ({"name": ""}, "ai_name_missing"),
        ({"kind": "OPENAI_COMPATIBLE"}, "ai_base_url_required"),
        ({"base_url": "ftp://x"}, "ai_base_url_invalid"),
    ):
        refused = create(world, **body)
        assert refused.status_code == 422, (body, refused.json())
        assert error_reason(refused.json()) == reason
    router = create(
        world,
        name="9router",
        kind="OPENAI_COMPATIBLE",
        base_url="http://9router:20128/v1/",
        model="cx/gpt-5.5",
    )
    assert router.status_code == 201, router.json()
    assert router.json()["endpoint"] == "http://9router:20128/v1"


async def test_13_no_secret_key_no_stored_key(world: World) -> None:
    screen(world, secret=False)
    world.act_as(world.owner)
    assert world.client.get("/api/ai").json()["secret_ready"] is False
    refused = create(world)
    assert refused.status_code == 422
    assert error_reason(refused.json()) == "ai_secret_not_configured"


async def test_14_routes_are_checked_and_follow_deletes(world: World) -> None:
    screen(world)
    world.act_as(world.owner)
    first = create(world).json()["id"]
    second = create(world, name="Gemini", kind="GEMINI", model="gemini-2.5-flash").json()["id"]
    third = create(world, name="OpenAI", kind="OPENAI", model="gpt-4o-mini").json()["id"]
    fourth = create(world, name="Khác", kind="OPENAI", model="gpt-4o").json()["id"]
    for routes, reason in (
        ({"DEFAULT": [first, first]}, "ai_route_duplicate"),
        ({"DEFAULT": [first, second, third, fourth]}, "ai_route_too_long"),
        ({"DEFAULT": [str(uuid.uuid4())]}, "ai_route_unknown_connection"),
    ):
        refused = world.client.put("/api/ai/routes", json={"routes": routes})
        assert refused.status_code == 422, refused.json()
        assert error_reason(refused.json()) == reason
    saved = world.client.put(
        "/api/ai/routes", json={"routes": {"DEFAULT": [second, first], "ORDER_SCRIPT": [first]}}
    )
    assert saved.status_code == 200, saved.json()
    routes = {item["purpose"]: item["connection_ids"] for item in saved.json()["routes"]}
    assert routes["DEFAULT"] == [second, first] and routes["ORDER_SCRIPT"] == [first]
    assert routes["ASSISTANT"] == []
    assert world.client.delete(f"/api/ai/connections/{first}").status_code == 204
    routes = {
        item["purpose"]: item["connection_ids"]
        for item in world.client.get("/api/ai").json()["routes"]
    }
    assert routes["DEFAULT"] == [second] and routes["ORDER_SCRIPT"] == []


async def test_15_the_connection_test_records_how_it_went(world: World) -> None:
    factory = screen(world)
    world.act_as(world.owner)
    made = create(world).json()
    tested = world.client.post(f"/api/ai/connections/{made['id']}/test")
    assert tested.status_code == 200, tested.json()
    assert tested.json()["ok"] is True
    assert tested.json()["connection"]["last_test_ok"] is True
    # The client got the decrypted key, the endpoint's model, no retries.
    assert factory.seen[-1]["api_key"] == KEY and factory.seen[-1]["max_retries"] == 0
    factory.fails = True
    failed = world.client.post(f"/api/ai/connections/{made['id']}/test")
    assert failed.status_code == 200
    assert failed.json()["ok"] is False
    assert "hết hạn mức" in failed.json()["message"]
    assert failed.json()["connection"]["last_test_message"] == failed.json()["message"]
    # A new key has not been tested.
    reset = world.client.patch(
        f"/api/ai/connections/{made['id']}", json={"api_key": "sk-new-key-9999"}
    )
    assert reset.json()["last_test_ok"] is None and reset.json()["api_key_hint"] == "••••9999"


async def test_16_the_loader_keeps_active_connections_in_order(world: World) -> None:
    screen(world)
    world.act_as(world.owner)
    first = create(world).json()["id"]
    second = create(world, name="Gemini", kind="GEMINI", model="gemini-2.5-flash").json()["id"]
    world.client.put("/api/ai/routes", json={"routes": {"DEFAULT": [second, first]}})
    built: list[str] = []

    def provider_for(row: AiConnection) -> Any:
        built.append(row.name)
        return Stub(row.name)

    table = await load_route_table(world.session, provider_for)
    assert [p.name for p in table.chains[AiPurpose.DEFAULT]] == ["Gemini", "DeepSeek chính"]
    world.client.patch(f"/api/ai/connections/{second}", json={"active": False})
    table = await load_route_table(world.session, provider_for)
    assert [p.name for p in table.chains[AiPurpose.DEFAULT]] == ["DeepSeek chính"]
