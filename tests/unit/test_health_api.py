"""Health endpoints and the service banner."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from meobot.api.deps import get_health_service
from meobot.api.main import create_app
from meobot.core.config import Settings
from tests.fakes import StubHealthService


@pytest.fixture
def stub_health() -> StubHealthService:
    return StubHealthService(healthy=True)


@pytest.fixture
def client(settings: Settings, stub_health: StubHealthService) -> Iterator[TestClient]:
    """A test client whose readiness probe never touches a real dependency."""
    app = create_app(settings)
    app.dependency_overrides[get_health_service] = lambda: stub_health
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_root_banner(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "MeoBot"
    assert "Telegram" in body["interface"]


def test_liveness_touches_no_dependency(client: TestClient) -> None:
    """/health/live must answer even when PostgreSQL is down."""
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "alive"


def test_readiness_reports_healthy(client: TestClient, stub_health: StubHealthService) -> None:
    response = client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["healthy"] is True
    assert body["status"] == "ready"
    assert {item["name"] for item in body["components"]} == {"postgres", "redis"}
    assert stub_health.calls == 1


def test_readiness_returns_503_when_degraded(settings: Settings) -> None:
    """A dependency outage must surface as 503, not a cheerful 200."""
    app = create_app(settings)
    app.dependency_overrides[get_health_service] = lambda: StubHealthService(healthy=False)
    with TestClient(app) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"


def test_request_id_header_is_returned(client: TestClient) -> None:
    response = client.get("/health/live")
    assert response.headers.get("X-Request-ID")


def test_inbound_request_id_is_preserved(client: TestClient) -> None:
    incoming = "3f1a7c2e-5b8d-4e6a-9c11-0a2b3c4d5e6f"
    response = client.get("/health/live", headers={"X-Request-ID": incoming})
    assert response.headers["X-Request-ID"] == incoming


def test_system_info_is_not_served_by_default(client: TestClient) -> None:
    """Since Step 1E.1 ``/api/v1/system/info`` is internal-only.

    It publishes the full tool registry - a map of everything the deployment can
    do - with no actor at all, so it is not part of the browser-facing surface.
    404, not 401: the route is absent rather than refused.
    """
    assert client.get("/api/v1/system/info").status_code == 404


def test_system_info_lists_registered_tools(settings: Settings) -> None:
    """The endpoint still works where it is meant to work.

    Built with the internal routers enabled, which is the only configuration that
    serves them - see ``docs/pr/STEP_1E1_WEB_SECURITY_HARDENING.md``.
    """
    app = create_app(settings.model_copy(update={"api_internal_routers_enabled": True}))
    app.dependency_overrides[get_health_service] = lambda: StubHealthService(healthy=True)
    with TestClient(app) as client:
        response = client.get("/api/v1/system/info")
    assert response.status_code == 200
    body = response.json()
    assert body["llm_provider"] == "fake"
    assert "system.health" in body["registered_tools"]
    assert body["telegram_configured"] is False
