"""The report assistant on Telegram ("Trợ lý AI", 2026-10-31).

Plan: ``docs/ai-assistant/BOT_REPORT_ASSISTANT_PLAN.md``.

* the words: which report a question is about, whether it asks for figures,
  which month it names;
* the permission: a module of its own, ticked per person by the OWNER alone
  (not an ADMIN, no department cell), which opens the whole bot and the
  report tools, and closes them again when taken back;
* the tools read the same numbers as the web screens - a figure in the reply
  is the figure in ``ToolResult.data`` and on ``/api/...``;
* the conversation: a report question reaches its tool even when the model
  is down or names none; a figures question naming no report is asked back;
  nobody without the grant gets a report;
* the voice: whoever holds it is "sếp" and the bot says "em", in the model's
  turns and in every sentence written without one.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.actor_profile_service import ActorProfileService
from meobot.application.assistant.reports import REPORT_OPENER
from meobot.application.conversation_service import ASSISTANT_UNKNOWN_REPLY, ConversationService
from meobot.application.org.bot_access import with_unified_access
from meobot.bot.commands import has_full_bot
from meobot.core.errors import LLMError
from meobot.domain.assistant.reports import (
    REPORT_TOOLS,
    ReportTool,
    area_question,
    asks_for_figures,
    month_in,
    report_topic,
)
from meobot.domain.board.models import TASK_STATES
from meobot.domain.identity.models import Actor, Role
from meobot.domain.org.modules import MODULES
from meobot.domain.permissions.matrix import Permission
from meobot.domain.policy.engine import PolicyEngine
from meobot.domain.policy.models import ActionPlan, DecisionCode, RiskLevel
from meobot.integrations.llm.base import PlanningRequest
from meobot.integrations.llm.fake import FakeLLMProvider
from meobot.tools.base import ToolContext, ToolRegistry
from meobot.tools.registry import build_default_registry
from tests.fakes import StubHealthService
from tests.unit.pr_world import World
from tests.unit.test_ads_department import D2, staffed
from tests.unit.test_order_commands import ads_world, create, person
from tests.unit.test_units_gating import error_reason

TODAY = date(2026, 10, 31)


class _WorldDatabase:
    """The world's one session behind the ``Database`` interface."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        yield self._session

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        yield self._session


def registry() -> ToolRegistry:
    return build_default_registry(health_service=StubHealthService())  # type: ignore[arg-type]


def conversation(world: World, llm: FakeLLMProvider | None = None) -> ConversationService:
    tools = registry()
    return ConversationService(
        llm=llm or FakeLLMProvider(),
        registry=tools,
        policy=PolicyEngine(tools.policies()),
        database=_WorldDatabase(world.session),  # type: ignore[arg-type]
        settings=world.settings,
    )


async def run(world: World, actor: Actor, name: str, **arguments: Any) -> Any:
    tool = registry().get(name)
    context = ToolContext(
        actor=actor, request_id=uuid.uuid4(), settings=world.settings, session=world.session
    )
    return await tool.execute(context, arguments)


def grant(world: World, viewer: Any, target: Any) -> Any:
    world.act_as(viewer)
    return world.client.post(
        f"/api/permissions/people/{target.id}/grants",
        json={"module": "ASSISTANT", "permission": "CHAT", "scope": "ALL"},
    )


async def granted(world: World, user: Any) -> Actor:
    made = grant(world, world.owner, user)
    assert made.status_code == 201, made.json()
    return await with_unified_access(world.session, world.actor(user))


# --- the words ------------------------------------------------------------------------


def test_01_the_words_pick_the_report_and_the_month() -> None:
    assert report_topic("Doanh thu tháng này bao nhiêu?") is ReportTool.ADS_REVENUE
    assert report_topic("chi phí quảng cáo tuần này") is ReportTool.ADS_REVENUE
    assert report_topic("KPI của Mạnh Ngọc đạt chưa") is ReportTool.ADS_KPI_BUDGET
    assert report_topic("ai vượt ngân sách tuần 3") is ReportTool.ADS_KPI_BUDGET
    assert report_topic("hiệu suất Ads tháng 9") is ReportTool.ADS_REVENUE
    assert report_topic("điểm hiệu suất phòng PR tháng 9") is ReportTool.PERFORMANCE
    assert report_topic("có bao nhiêu order trễ hạn") is ReportTool.ORDERS
    assert report_topic("task PR đang chờ duyệt bao nhiêu") is ReportTool.PR
    assert report_topic("tình hình order hôm nay") is ReportTool.ORDERS
    # Everyday words alone are conversation, not a report.
    assert report_topic("Viết cho mình 3 ý tưởng video tuần này") is None
    assert report_topic("task PR đang chờ duyệt") is None
    assert not asks_for_figures("tình hình nội dung tuần này thế nào")
    assert report_topic("tài nguyên nào đang cảnh báo đỏ") is ReportTool.RESOURCES
    assert report_topic("tổng hợp tình hình công ty hôm nay") is ReportTool.COMPANY
    assert report_topic("prompt hay cho kịch bản") is None  # "pr" is a whole word only
    assert report_topic("chào bạn") is None
    assert asks_for_figures("Bao nhiêu rồi?") and asks_for_figures("số liệu hôm nay")
    assert not asks_for_figures("viết giúp mình một caption")
    assert month_in("doanh thu tháng này", TODAY) == "2026-10"
    assert month_in("tháng trước", TODAY) == "2026-09"
    assert month_in("tháng trước", date(2026, 1, 5)) == "2025-12"
    assert month_in("điểm tháng 9", TODAY) == "2026-09"
    assert month_in("tháng 12", TODAY) == "2025-12"  # a month still to come is last year's
    assert month_in("số liệu 09/2026", TODAY) == "2026-09"
    assert month_in("2026-08", TODAY) == "2026-08"
    assert month_in("hôm nay", TODAY) is None
    assert set(REPORT_TOOLS) == {tool.value for tool in ReportTool}
    assert "doanh thu" in area_question()


# --- the permission -------------------------------------------------------------------


async def test_02_only_the_owner_ticks_it_and_it_opens_the_bot(world: World) -> None:
    spec = MODULES["ASSISTANT"]
    assert spec.owner_only == {"CHAT"} and spec.department_keys() == []
    admin = await person(world, "Quản trị", Role.ADMIN)
    # Not a department cell: "Phòng ban" does not offer it.
    world.act_as(world.owner)
    catalog = world.client.get("/api/departments/catalog").json()
    assert "ASSISTANT" not in [item["module"] for item in catalog["modules"]]
    # An ADMIN neither holds it by role nor may give it.
    admin_actor = await with_unified_access(world.session, world.actor(admin))
    assert not admin_actor.full_bot
    assert Permission.ASSISTANT_REPORT.value not in admin_actor.extra_permissions
    # Nor sees the page it sits on: a person's "Quyền" is the OWNER's alone.
    world.act_as(admin)
    hidden = world.client.get(f"/api/permissions/people/{world.member.id}")
    assert hidden.status_code == 403
    refused = grant(world, admin, world.member)
    assert refused.status_code == 403
    assert error_reason(refused.json()) == "permission_forbidden"
    assert world.client.get("/api/units/me").json()["assistant"]["chat"] is False
    world.act_as(world.owner)
    overview = world.client.get(f"/api/permissions/people/{world.member.id}").json()
    module = next(item for item in overview["modules"] if item["module"] == "ASSISTANT")
    assert module["label"] == "Trợ lý AI" and module["grantable"] == {"CHAT": ["ALL"]}
    # The OWNER ticks the member: the whole bot and the report tools.
    before = await with_unified_access(world.session, world.actor(world.member))
    assert not has_full_bot(before)
    member = await granted(world, world.member)
    assert has_full_bot(member)
    assert Permission.ASSISTANT_REPORT.value in member.extra_permissions
    names = {tool.name for tool in registry().visible_to(member)}
    assert names >= REPORT_TOOLS
    assert not REPORT_TOOLS & {tool.name for tool in registry().visible_to(before)}
    world.act_as(world.member)
    assert world.client.get("/api/units/me").json()["assistant"]["chat"] is True
    world.act_as(world.owner)
    assert world.client.get("/api/units/me").json()["assistant"]["chat"] is True
    # Taken back by the OWNER alone.
    personal = world.client.get(f"/api/permissions/people/{world.member.id}").json()["personal"]
    grant_id = next(item["id"] for item in personal if item["module"] == "ASSISTANT")
    path = f"/api/permissions/people/{world.member.id}/grants/{grant_id}"
    world.act_as(admin)
    assert world.client.delete(path).status_code == 403
    world.act_as(world.owner)
    assert world.client.delete(path).status_code == 204
    after = await with_unified_access(world.session, world.actor(world.member))
    assert not has_full_bot(after)
    assert Permission.ASSISTANT_REPORT.value not in after.extra_permissions
    # The OWNER ticks it for an ADMIN too, and unticks it (the checklist's save).
    checklist = f"/api/permissions/people/{admin.id}/grants"
    ticked = world.client.put(
        checklist, json={"module": "ASSISTANT", "permissions": {"CHAT": "ALL"}}
    )
    assert ticked.status_code == 200, ticked.json()
    assert has_full_bot(await with_unified_access(world.session, world.actor(admin)))
    unticked = world.client.put(
        checklist, json={"module": "ASSISTANT", "permissions": {"CHAT": None}}
    )
    assert unticked.status_code == 200, unticked.json()
    assert not has_full_bot(await with_unified_access(world.session, world.actor(admin)))


async def test_03_the_policy_refuses_a_report_to_anybody_not_ticked(world: World) -> None:
    admin = await person(world, "Quản trị", Role.ADMIN)
    service = conversation(world)
    plan = ActionPlan(
        intent="report",
        risk_level=RiskLevel.LOW,
        requires_confirmation=False,
        tool_name=ReportTool.ADS_REVENUE.value,
        arguments={},
    )
    for actor in (
        await with_unified_access(world.session, world.actor(admin)),
        await with_unified_access(world.session, world.actor(world.lead)),  # the PR head
    ):
        reply = await service.execute_plan(actor=actor, plan=plan, request_id=uuid.uuid4())
        assert reply.decision is not None
        assert reply.decision.code is DecisionCode.MISSING_PERMISSION
        assert reply.tool_result is None


# --- the tools read the web's numbers -------------------------------------------------------


async def test_04_the_order_report_counts_what_the_board_lists(world: World) -> None:
    ads = await ads_world(world)
    create(world, ads)
    create(world, ads, title="Kịch bản B")
    owner = world.actor(world.owner)
    result = await run(world, owner, ReportTool.ORDERS.value, state="cho_duyet")
    world.act_as(world.owner)
    for state in TASK_STATES:
        listed = world.client.get("/api/board/tasks", params={"unit": "ADS", "state": state})
        assert result.data["states"][state] == listed.json()["total"], state
    assert result.data["states"]["CHO_DUYET"] == 2
    assert len(result.data["state_rows"]) == 2
    assert "Chờ duyệt 2" in result.message
    assert "https://pr.example.com/tasks?unit=ADS&state=CHO_DUYET" in result.message
    # A state outside the list is refused, never guessed.
    with pytest.raises(Exception, match="Tham số không hợp lệ"):
        await run(world, owner, ReportTool.ORDERS.value, state="XONG_ROI")


async def test_05_the_revenue_report_is_the_team_dashboard(world: World) -> None:
    _, ngoc, _ = await staffed(world)
    entries = {f"u:{ngoc.id}": 5_400_000, f"u:{ngoc.id}:hn": 1_080_000}
    world.client.put("/api/ads/revenue", json={"day": D2.isoformat(), "entries": entries})
    world.act_as(world.owner)
    team = world.client.get("/api/ads/team", params={"month": "2026-10"}).json()
    result = await run(
        world, world.actor(world.owner), ReportTool.ADS_REVENUE.value, month="10/2026"
    )
    now = result.data["current"]
    assert now["revenue"] == team["current"]["revenue"] == 6_480_000
    assert now["spend"] == team["current"]["spend"]
    assert "6.480.000 đ" in result.message
    assert result.data["top_marketers"][0]["name"] == ngoc.full_name
    # A month with nothing typed says so - no number is made up.
    empty = await run(
        world, world.actor(world.owner), ReportTool.ADS_REVENUE.value, month="2026-08"
    )
    assert empty.data["current"]["revenue"] is None
    assert "Doanh thu tổng (thực thu, xét KPI): chưa có dữ liệu" in empty.message
    kpi = await run(
        world, world.actor(world.owner), ReportTool.ADS_KPI_BUDGET.value, person="mạnh ngọc"
    )
    assert [line["name"] for line in kpi.data["marketers"]] == [ngoc.full_name]
    missing = await run(
        world, world.actor(world.owner), ReportTool.ADS_KPI_BUDGET.value, person="X"
    )
    assert missing.data["found"] is False


async def test_06_every_report_runs_on_a_quiet_world_and_says_so(world: World) -> None:
    await ads_world(world)
    owner = world.actor(world.owner)
    points = await run(world, owner, ReportTool.PERFORMANCE.value)
    assert points.data["points"] == 0 and "chưa có dữ liệu" in points.message
    one = await run(world, owner, ReportTool.PERFORMANCE.value, person="Hảo")
    assert one.data["name"] == "Hảo" and one.data["points"] == 0
    nobody = await run(world, owner, ReportTool.PERFORMANCE.value, department="Không có")
    assert nobody.data["found"] is False
    resources = await run(world, owner, ReportTool.RESOURCES.value)
    assert resources.data["total"] == 0 and resources.data["open_requests"] == {}
    pr = await run(world, owner, ReportTool.PR.value)
    assert pr.data["published"] == 0
    company = await run(world, owner, ReportTool.COMPANY.value, month="tháng này")
    assert set(company.data) >= {"ads", "orders", "performance", "pr", "resources"}
    assert all(
        company.data[key] is not None for key in ("ads", "orders", "performance", "pr", "resources")
    )
    assert "không lấy được" not in company.message


# --- the conversation ------------------------------------------------------------------------


class _DownPlanner(FakeLLMProvider):
    async def plan_tool_action(self, request: PlanningRequest) -> ActionPlan:
        raise LLMError("planner down")


async def test_07_a_report_question_reaches_its_tool_even_with_the_model_down(
    world: World,
) -> None:
    ads = await ads_world(world)
    create(world, ads)
    member = await granted(world, world.member)
    # (The fake model knows no report: it routes this to chat, then cannot plan.)
    for llm in (FakeLLMProvider(), _DownPlanner(fail_route=True)):
        reply = await conversation(world, llm).handle_message(
            actor=member, message="Tình hình order hôm nay ra sao?", request_id=uuid.uuid4()
        )
        assert reply.executed_a_tool, reply.text
        assert reply.plan is not None and reply.plan.tool_name == ReportTool.ORDERS.value
        assert reply.data["states"]["CHO_DUYET"] == 1
    # The month the message names is the month read.
    reply = await conversation(world, _DownPlanner(fail_route=True)).handle_message(
        actor=member, message="điểm hiệu suất tháng 9/2026", request_id=uuid.uuid4()
    )
    assert reply.data["month"] == "2026-09"
    # Figures without a report: asked back, nothing generated.
    llm = FakeLLMProvider()
    asked = await conversation(world, llm).handle_message(
        actor=member, message="Bao nhiêu rồi em?", request_id=uuid.uuid4()
    )
    assert asked.text == area_question() and not asked.executed_a_tool
    assert llm.seen_chats == []


async def test_08_nobody_else_gets_a_report(world: World) -> None:
    ads = await ads_world(world)
    create(world, ads)
    plain = await with_unified_access(world.session, world.actor(ads.head))
    reply = await conversation(world).handle_message(
        actor=plain, message="Tình hình order hôm nay ra sao?", request_id=uuid.uuid4()
    )
    assert not (reply.plan and reply.plan.tool_name in REPORT_TOOLS)
    assert "states" not in reply.data


# --- the voice ------------------------------------------------------------------------


async def test_09_the_assistant_says_em_to_sep(world: World) -> None:
    ads = await ads_world(world)
    create(world, ads)
    member = await granted(world, world.member)
    plain = await with_unified_access(world.session, world.actor(world.lead))
    profiles = ActorProfileService(world.session, world.settings)
    # Who holds "Trợ lý AI" is "sếp", the bot says "em"; nobody else changes.
    mine = await profiles.profile_for(member)
    assert (mine.address, mine.own_address) == ("sếp", "em")
    assert "gọi người dùng là 'sếp', TasksBot xưng 'em'" in mine.render_user_block()
    theirs = await profiles.profile_for(plain)
    assert (theirs.address, theirs.own_address) == ("bạn", "mình")
    # Even over a stored form of address, which comes back once it is taken away.
    telegram_id = 880_001
    stored = member.model_copy(update={"telegram_user_id": telegram_id})
    await profiles.set_preferred_address(stored, "anh")
    assert (await profiles.profile_for(stored)).address == "sếp"
    bare = world.actor(world.member).model_copy(update={"telegram_user_id": telegram_id})
    assert (await profiles.profile_for(bare)).address == "anh"
    # The model is told so on a chat turn.
    llm = FakeLLMProvider()
    await conversation(world, llm).handle_message(
        actor=member, message="viết giúp em một caption ngắn", request_id=uuid.uuid4()
    )
    told = llm.seen_chats[-1].prompt_context
    assert "TasksBot xưng 'em'" in told and 'luôn xưng "em"' in told
    await conversation(world, llm).handle_message(
        actor=plain, message="viết giúp mình một caption ngắn", request_id=uuid.uuid4()
    )
    told = llm.seen_chats[-1].prompt_context
    assert "TasksBot xưng 'mình'" in told and 'luôn xưng "em"' not in told
    # The sentences written without a model speak the same way.
    reply = await conversation(world).handle_message(
        actor=member, message="Tình hình order hôm nay ra sao?", request_id=uuid.uuid4()
    )
    assert reply.text.startswith(REPORT_OPENER)
    assert "Sếp đối chiếu trên web: https://pr.example.com/tasks?unit=ADS" in reply.text
    assert "Sếp muốn xem mảng nào ạ?" in area_question()
    unknown = ActionPlan(intent="unknown", risk_level=RiskLevel.LOW, requires_confirmation=False)
    answered = await conversation(world).execute_plan(
        actor=member, plan=unknown, request_id=uuid.uuid4()
    )
    assert answered.text == ASSISTANT_UNKNOWN_REPLY
