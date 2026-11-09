"""Agents read APM figures (2026-11-09): ``/api/agents/gateway`` + the OWNER's ticks.

* the door: closed (404) without a token; wrong token 401; an IP not on the
  list 403 (Cloudflare's ``CF-Connecting-IP``); an unknown agent 403;
* each agent reads its own areas only (defaults by role, then the OWNER's
  ticks); a refused area 403, bad arguments 422; every read audited without
  the figures;
* the admin grid: OWNER reads and ticks, anybody else 403.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from meobot.api.deps import get_app_settings
from meobot.application.agents.service import seed_default_agents
from meobot.db.models.audit_log import AuditLog
from tests.unit.pr_world import World

pytestmark = pytest.mark.asyncio

TOKEN = "gateway-token-" + "x" * 30


def open_door(world: World, *, token: str | None = TOKEN, ips: str = "") -> None:
    settings = world.settings.model_copy(
        update={
            "agent_gateway_token": SecretStr(token) if token else None,
            "agent_gateway_allowed_ips": ips,
            "web_base_url": "https://app.example.vn",
        }
    )
    app = world.client.app  # type: ignore[attr-defined]
    app.dependency_overrides[get_app_settings] = lambda: settings


def ask(
    world: World,
    path: str,
    *,
    agent: str = "cfo",
    token: str = TOKEN,
    ip: str | None = None,
    body: dict[str, Any] | None = None,
) -> Any:
    headers = {"Authorization": f"Bearer {token}", "X-Agent": agent}
    if ip:
        headers["CF-Connecting-IP"] = ip
    if body is None and path.endswith("/areas"):
        return world.client.get(f"/api/agents/gateway{path}", headers=headers)
    return world.client.post(f"/api/agents/gateway{path}", headers=headers, json=body or {})


def reason(response: Any) -> str:
    return str(response.json()["error"]["details"]["reason"])


async def test_01_the_door_needs_the_token_the_ip_and_a_known_agent(world: World) -> None:
    await seed_default_agents(world.session)
    open_door(world, token=None)
    assert ask(world, "/areas").status_code == 404
    open_door(world)
    wrong = ask(world, "/areas", token="nope")
    assert wrong.status_code == 401 and reason(wrong) == "gateway_unauthorized"
    unknown = ask(world, "/areas", agent="ai-do")
    assert unknown.status_code == 403 and reason(unknown) == "agent_unknown"
    open_door(world, ips="43.240.117.204, 10.0.0.0/8")
    assert reason(ask(world, "/areas")) == "gateway_ip_forbidden"  # no Cloudflare header
    assert ask(world, "/areas", ip="1.2.3.4").status_code == 403
    assert ask(world, "/areas", ip="43.240.117.204").status_code == 200
    assert ask(world, "/areas", ip="10.1.2.3").status_code == 200


async def test_02_each_agent_reads_its_own_areas(world: World) -> None:
    await seed_default_agents(world.session)
    open_door(world)
    cfo = ask(world, "/areas").json()
    assert cfo["agent"] == "cfo"
    assert [area["key"] for area in cfo["areas"]] == [
        "COMPANY",
        "ADS_REVENUE",
        "ADS_KPI_BUDGET",
        "RESOURCES",
    ]
    assert ask(world, "/areas", agent="phapche").json()["areas"] == []
    report = ask(world, "/reports/ads_revenue", body={"month": "2026-10"})
    assert report.status_code == 200, report.json()
    got = report.json()
    assert got["area"] == "ADS_REVENUE" and got["title"]
    assert got["text"].startswith(f"**{got['title']}**") and "sếp" not in got["text"]
    assert got["link"] is None or got["link"].startswith("https://app.example.vn/")
    refused = ask(world, "/reports/PERFORMANCE")
    assert refused.status_code == 403 and reason(refused) == "data_area_forbidden"
    assert refused.json()["error"]["details"]["allowed"][0] == "COMPANY"
    bad = ask(world, "/reports/ADS_REVENUE", body={"month": "mười ba"})
    assert bad.status_code == 422 and reason(bad) == "data_arguments_invalid"
    assert ask(world, "/reports/LUONG").status_code == 422
    logs = (
        await world.session.scalars(select(AuditLog).where(AuditLog.action == "agent.data.read"))
    ).all()
    assert len(logs) == 1
    assert logs[0].entity_id == "cfo"
    assert logs[0].after_data == {"area": "ADS_REVENUE", "arguments": {"month": "2026-10"}}


async def test_03_the_owner_ticks_what_an_agent_reads(world: World) -> None:
    await seed_default_agents(world.session)
    open_door(world)
    world.act_as(world.member)
    assert world.client.get("/api/agents/admin/data").status_code == 403
    assert world.client.put("/api/agents/admin/data/cfo", json={"areas": ["PR"]}).status_code == 403
    world.act_as(world.owner)
    grid = world.client.get("/api/agents/admin/data").json()
    assert grid["gateway_configured"] is True
    assert len(grid["areas"]) == 7 and grid["agents"]["phapche"] == []
    saved = world.client.put(
        "/api/agents/admin/data/phapche", json={"areas": ["performance", "PR", "nope", "PR"]}
    )
    assert saved.status_code == 200 and saved.json()["areas"] == ["PERFORMANCE", "PR"]
    assert world.client.put("/api/agents/admin/data/ai-do", json={"areas": []}).status_code == 404
    assert [a["key"] for a in ask(world, "/areas", agent="phapche").json()["areas"]] == [
        "PERFORMANCE",
        "PR",
    ]
    assert ask(world, "/reports/PR", agent="phapche").status_code == 200
    assert world.client.get("/api/agents/admin/data").json()["agents"]["phapche"] == [
        "PERFORMANCE",
        "PR",
    ]
    logs = (
        await world.session.scalars(
            select(AuditLog).where(AuditLog.action == "agent.data.areas_updated")
        )
    ).all()
    assert len(logs) == 1 and logs[0].after_data == {"areas": ["PERFORMANCE", "PR"]}
