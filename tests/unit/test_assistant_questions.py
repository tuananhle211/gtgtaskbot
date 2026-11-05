"""The bosses' questions to the report assistant (2026-11-04).

The list given to the bosses is ``docs/ai-assistant/BOSS_QUESTIONS.md``; the
cases below are that list, so a question there that stops reaching its report
- or its explanation - fails here:

* a question for figures reaches its report, with the day, span, month or
  order state it names, whatever the model says (the model is not asked);
* a follow-up naming only a period asks the report the thread just read;
* a question about how the system works ("Ads / Doanh thu tính thế nào?") is
  answered by the skill's own definition, word for word, no model involved;
* the skill sits in the prompt of whoever holds the assistant, nobody else.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

import pytest

from meobot.application.ads.sync_service import vn_today
from meobot.application.org.bot_access import with_unified_access
from meobot.domain.assistant.reports import ReportTool
from meobot.domain.assistant.skill import TERMS, explain, skill_block, term_for
from meobot.domain.conversations.references import RecentReference
from meobot.integrations.llm.base import MessageRoute, RouteMode
from meobot.integrations.llm.fake import FakeLLMProvider
from tests.unit.pr_world import World
from tests.unit.test_ads_department import D1, D2, staffed
from tests.unit.test_assistant_reports import conversation, granted, run

TODAY = vn_today()
MONDAY = TODAY - timedelta(days=TODAY.weekday())
THIS_MONTH = f"{TODAY.year:04d}-{TODAY.month:02d}"
OCTOBER = f"{TODAY.year if TODAY.month >= 10 else TODAY.year - 1:04d}-10"


def _day(value: date) -> str:
    return value.isoformat()


#: (question, report, arguments read off the question).
FIGURES: tuple[tuple[str, ReportTool, dict[str, Any]], ...] = (
    # Doanh thu & Ads
    ("Doanh thu hôm nay bao nhiêu?", ReportTool.ADS_REVENUE, {"day": _day(TODAY)}),
    (
        "Doanh thu hôm qua thế nào?",
        ReportTool.ADS_REVENUE,
        {"day": _day(TODAY - timedelta(days=1))},
    ),
    (
        "Doanh thu tuần này",
        ReportTool.ADS_REVENUE,
        {"day": _day(MONDAY), "day_to": _day(TODAY)},
    ),
    (
        "Doanh thu tuần trước bao nhiêu",
        ReportTool.ADS_REVENUE,
        {"day": _day(MONDAY - timedelta(days=7)), "day_to": _day(MONDAY - timedelta(days=1))},
    ),
    (
        "Doanh thu 7 ngày qua",
        ReportTool.ADS_REVENUE,
        {"day": _day(TODAY - timedelta(days=6)), "day_to": _day(TODAY)},
    ),
    ("Doanh thu tháng này đến giờ được bao nhiêu?", ReportTool.ADS_REVENUE, {"month": THIS_MONTH}),
    ("Doanh thu tháng 10", ReportTool.ADS_REVENUE, {"month": OCTOBER}),
    ("So với tháng trước doanh thu tăng hay giảm?", ReportTool.ADS_REVENUE, {}),
    ("Chi phí quảng cáo hôm nay", ReportTool.ADS_REVENUE, {"day": _day(TODAY)}),
    ("Chi tiêu Ads tháng này bao nhiêu", ReportTool.ADS_REVENUE, {"month": THIS_MONTH}),
    ("Tỉ lệ Ads / Doanh thu tháng này", ReportTool.ADS_REVENUE, {"month": THIS_MONTH}),
    ("Doanh thu Hà Nội và Sài Gòn tháng này", ReportTool.ADS_REVENUE, {"month": THIS_MONTH}),
    ("Marketer nào doanh thu cao nhất tháng này?", ReportTool.ADS_REVENUE, {"month": THIS_MONTH}),
    ("Hôm nay ai ra doanh thu nhiều nhất?", ReportTool.ADS_REVENUE, {"day": _day(TODAY)}),
    # (the person it names is read by ``_named_person``: test_assistant_reports::test_11)
    ("Doanh thu của Đào Đình Dương hôm nay", ReportTool.ADS_REVENUE, {"day": _day(TODAY)}),
    ("Doanh thu tháng này của Nguyễn Quang Nguyên", ReportTool.ADS_REVENUE, {"month": THIS_MONTH}),
    ("Bao nhiêu số điện thoại hôm nay?", ReportTool.ADS_REVENUE, {"day": _day(TODAY)}),
    ("Chi phí trên mỗi SĐT tháng này", ReportTool.ADS_REVENUE, {"month": THIS_MONTH}),
    # KPI & ngân sách
    ("KPI tháng này đạt bao nhiêu phần trăm?", ReportTool.ADS_KPI_BUDGET, {"month": THIS_MONTH}),
    ("KPI của Mạnh Ngọc", ReportTool.ADS_KPI_BUDGET, {}),
    ("Ai đang vượt ngân sách tuần?", ReportTool.ADS_KPI_BUDGET, {}),
    ("Ngân sách tuần này thế nào", ReportTool.ADS_KPI_BUDGET, {"month": THIS_MONTH}),
    # Order
    ("Có bao nhiêu order đang chờ duyệt?", ReportTool.ORDERS, {}),
    ("Order nào đang trễ hạn?", ReportTool.ORDERS, {}),
    ("Tình hình order hôm nay ra sao?", ReportTool.ORDERS, {"month": THIS_MONTH}),
    ("Liệt kê order bị trả lại", ReportTool.ORDERS, {"state": "ORDER_RETURNED"}),
    ("Order nào đang chờ phân công?", ReportTool.ORDERS, {"state": "CHO_PHAN_CONG"}),
    (
        "Tháng này đặt bao nhiêu order, hoàn thành bao nhiêu?",
        ReportTool.ORDERS,
        {"month": THIS_MONTH},
    ),
    # Điểm hiệu suất
    ("Điểm hiệu suất tháng này", ReportTool.PERFORMANCE, {"month": THIS_MONTH}),
    ("Điểm phòng Dựng tháng 10", ReportTool.PERFORMANCE, {"month": OCTOBER}),
    ("Điểm của Quỳnh Như tháng này", ReportTool.PERFORMANCE, {"month": THIS_MONTH}),
    ("Ai điểm cao nhất tháng này?", ReportTool.PERFORMANCE, {"month": THIS_MONTH}),
    ("Phòng nào đạt định mức?", ReportTool.PERFORMANCE, {}),
    # PR
    ("Tình hình PR tháng này", ReportTool.PR, {"month": THIS_MONTH}),
    ("Tháng này đăng được bao nhiêu bài?", ReportTool.PR, {"month": THIS_MONTH}),
    ("Task PR đang ở pha nào nhiều nhất?", ReportTool.PR, {}),
    # Tài nguyên
    ("Tình hình tài nguyên", ReportTool.RESOURCES, {}),
    ("Có bao nhiêu tài nguyên đang cảnh báo đỏ?", ReportTool.RESOURCES, {}),
    ("Đề xuất mua tài nguyên nào đang chờ?", ReportTool.RESOURCES, {}),
    # Tổng hợp
    ("Tổng hợp tình hình công ty tháng này", ReportTool.COMPANY, {"month": THIS_MONTH}),
    ("Báo cáo A-Z tháng 10", ReportTool.COMPANY, {"month": OCTOBER}),
)

#: (question, the term that answers it).
EXPLAINED: tuple[tuple[str, str], ...] = (
    ("Doanh thu thực thu là gì?", "Doanh thu tổng (thực thu)"),
    ("Doanh thu lấy từ đâu ra?", "Doanh thu tổng (thực thu)"),
    ("DT hoa hồng là gì?", "Doanh thu hoa hồng"),
    ("Ads / Doanh thu tính thế nào?", "Ads / Doanh thu"),
    ("Doanh thu Marketing khác doanh thu Cơ sở thế nào?", "Doanh thu Marketing và doanh thu Cơ sở"),
    ("Chi tiêu với lập hoá đơn khác gì?", "Chi tiêu và lập hoá đơn"),
    ("Hiệu suất theo cơ sở tính sao?", "Hiệu suất theo cơ sở"),
    ("BM là gì?", "BM"),
    ("SĐT là gì?", "Số điện thoại (SĐT)"),
    ("Hotline tính vào đâu?", "Hotline và marketer đã nghỉ"),
    ("KPI tính như thế nào?", "KPI doanh thu"),
    ("Ngân sách tuần chia thế nào?", "Ngân sách tuần"),
    ("Order là gì?", "Order (task ORD)"),
    ("Ai duyệt order?", "Ai duyệt order"),
    ("Đang sửa là gì?", "Trạng thái order"),
    ("Order sinh nhật là gì?", "Order sinh nhật"),
    ("Chờ phân công là gì?", "Trạng thái order"),
    ("Trễ hạn nghĩa là gì?", "Trễ hạn"),
    ("Huỷ order và trả order khác gì?", "Huỷ và trả order"),
    ("Điểm hiệu suất tính thế nào?", "Điểm hiệu suất"),
    ("Hình thức Adapt là gì?", "Hình thức (% điểm)"),
    ("Định mức là gì?", "Định mức"),
    ("Hệ số điểm là gì?", "Hệ số điểm"),
    ("Điểm PR tính thế nào?", "Điểm PR và Seeding"),
    ("Task PR đi qua những pha nào?", "Task PR"),
    ("Cảnh báo tài nguyên là gì?", "Cảnh báo tài nguyên"),
    ("Đề xuất mua tài nguyên quy trình thế nào?", "Đề xuất tài nguyên"),
    ("Phân quyền hoạt động thế nào?", "Phân quyền"),
    ("Em hỏi được gì?", "Trợ lý AI"),
)


async def _owner(world: World) -> Any:
    return await with_unified_access(world.session, world.actor(world.owner))


@pytest.mark.parametrize(("question", "report", "arguments"), FIGURES)
async def test_01_a_figures_question_reaches_its_report(
    world: World, question: str, report: ReportTool, arguments: dict[str, Any]
) -> None:
    service = conversation(world)
    route, canned = service._report_route(
        await _owner(world), question, MessageRoute.chat(), None, ()
    )
    assert route.mode is RouteMode.TOOL and canned is None, question
    assert route.possible_tool_name == report.value, question
    assert service._report_period(report.value, question) == arguments, question


@pytest.mark.parametrize(
    ("question", "arguments"),
    (
        ("Hôm nay thôi", {"day": _day(TODAY)}),
        ("Lấy tôi số liệu hôm qua thôi", {"day": _day(TODAY - timedelta(days=1))}),
        ("Còn tuần này?", {"day": _day(MONDAY), "day_to": _day(TODAY)}),
        ("còn tháng 10?", {"month": OCTOBER}),
    ),
)
async def test_02_a_follow_up_asks_the_same_report_again(
    world: World, question: str, arguments: dict[str, Any]
) -> None:
    service = conversation(world)
    last = (RecentReference(entity_type="report", entity_id=ReportTool.ADS_REVENUE.value),)
    route, _ = service._report_route(await _owner(world), question, MessageRoute.chat(), None, last)
    assert route.possible_tool_name == ReportTool.ADS_REVENUE.value
    assert service._report_period(ReportTool.ADS_REVENUE.value, question) == arguments


@pytest.mark.parametrize(("question", "name"), EXPLAINED)
async def test_03_a_how_question_gets_the_skill_s_answer_without_a_model(
    world: World, question: str, name: str
) -> None:
    term = term_for(question)
    assert term is not None and term.name == name, question
    llm = FakeLLMProvider()
    owner = await _owner(world)
    reply = await conversation(world, llm).handle_message(
        actor=owner, message=question, request_id=uuid.uuid4()
    )
    assert reply.text == explain(term, world.settings.web_base_url), question
    assert not reply.executed_a_tool and llm.seen_chats == []


async def test_04_every_term_is_reachable_and_answers_in_the_voice() -> None:
    for term in TERMS:
        assert term_for(f"{term.name} là gì?") is term, term.name
        assert explain(term).startswith(f"Dạ, **{term.name}**: ")
    block = skill_block("Ads / Doanh thu tính thế nào?")
    assert "report.ads_revenue" in block and "- Ads / Doanh thu: " in block
    assert len(block) < 4000


async def test_05_the_skill_is_in_the_holder_s_prompt_only(world: World) -> None:
    member = await granted(world, world.member)
    plain = await with_unified_access(world.session, world.actor(world.lead))
    llm = FakeLLMProvider()
    await conversation(world, llm).handle_message(
        actor=member, message="viết giúp em caption ngắn", request_id=uuid.uuid4()
    )
    assert "[ASSISTANT SKILL]" in llm.seen_chats[-1].prompt_context
    await conversation(world, llm).handle_message(
        actor=plain, message="viết giúp mình caption ngắn", request_id=uuid.uuid4()
    )
    assert "[ASSISTANT SKILL]" not in llm.seen_chats[-1].prompt_context
    # Nobody without the grant gets a definition from the skill either.
    reply = await conversation(world).handle_message(
        actor=plain, message="Ads / Doanh thu tính thế nào?", request_id=uuid.uuid4()
    )
    assert not reply.text.startswith("Dạ, **Ads / Doanh thu**")


async def test_06_a_span_s_revenue_is_the_team_dashboard_over_those_days(world: World) -> None:
    _, ngoc, _ = await staffed(world)
    for day, amount in ((D1, 1_000_000), (D2, 5_400_000)):
        world.client.put(
            "/api/ads/revenue", json={"day": day.isoformat(), "entries": {f"u:{ngoc.id}": amount}}
        )
    world.act_as(world.owner)
    team = world.client.get(
        "/api/ads/team", params={"start": D1.isoformat(), "end": D2.isoformat()}
    ).json()
    result = await run(
        world,
        world.actor(world.owner),
        ReportTool.ADS_REVENUE.value,
        day=D1.isoformat(),
        day_to=D2.isoformat(),
    )
    assert result.data["current"]["revenue"] == team["current"]["revenue"] == 6_400_000
    assert (result.data["from"], result.data["day"]) == (D1.isoformat(), D2.isoformat())
    assert f"**Doanh thu Ads · {D1:%d/%m} - {D2:%d/%m/%Y} (2 ngày)**" in result.message
    assert "2 ngày trước đó: chưa có dữ liệu" in result.message
