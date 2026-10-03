"""KPI workload visibility: one calculator, per-quota provenance, batched lists.

Seven sections, in the order the request listed its tests:

1. **Core arithmetic** - ITEM_COUNT and QUANTITY quotas priced per one unit,
   summed, quantized like M6, and *not* 600 x 120.
2. **Incomplete pricing** - one unpriced quota marks the plan incomplete, is
   named, and withholds the percentage without hiding the priced minutes.
3. **Target minutes** - September 2026 is 22 weekdays x 300 = 6 600 from the
   canonical schedule; no schedule means minutes with no percentage.
4. **Approved and draft** - two figures, never merged, and the draft's takes
   over only on approval.
5. **Manager list** - every employee priced, nobody at a false 0%, in a fixed
   number of statements however many employees.
6. **Detail** - rule label, contribution, measurement mode, quantity basis and
   the missing-rule mark on the wire.
7. **Config** - the rule list carries its basis; employees cannot edit rules.
"""

# ruff: noqa: F811 - ``world`` is a fixture reused across the work suites.
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import event

from meobot.application.pr_plan_workload import QuotaWorkloadStatus, price_quota
from meobot.db.models.pr_work_quota import PrWorkQuota
from meobot.db.models.user import User
from meobot.domain.identity.models import Role
from meobot.domain.pr.errors import PrPermissionDeniedError
from meobot.domain.pr.performance import PrWorkScoringMode
from meobot.domain.pr.work import PrWorkUnit
from meobot.domain.pr.work_quota import PrWorkQuotaBasis
from meobot.domain.pr.work_quota_labels import format_minutes, workload_rule_label
from tests.unit.test_pr_kpi_self_service import add, approve, self_draft, submit
from tests.unit.test_pr_performance import policy, rule, schedule
from tests.unit.test_pr_production_lifecycle import World, world  # noqa: F401
from tests.unit.test_pr_work_quota import approved_plan, month, seeding_type, work_type

pytestmark = pytest.mark.asyncio


async def ready(world: World):  # type: ignore[no-untyped-def]
    """A Monday-to-Friday calendar, a 300-minute policy, September 2026."""
    await schedule(world)
    await policy(world)
    return await month(world)


def detail_of(world: World, plan_id: uuid.UUID, *, as_user: User | None = None) -> dict:  # type: ignore[type-arg]
    world.act_as(as_user or world.owner)
    response = world.client.get(f"/api/pr/work/plans/{plan_id}")
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def summary_rows(world: World, period_id: uuid.UUID) -> list[dict]:  # type: ignore[type-arg]
    world.act_as(world.owner)
    response = world.client.get("/api/pr/work/plans/summary", params={"period_id": str(period_id)})
    assert response.status_code == 200, response.text
    return response.json()["items"]  # type: ignore[no-any-return]


# ===========================================================================
# 1. Core arithmetic
# ===========================================================================


async def test_1_2_an_item_count_quota_is_target_times_minutes_per_item(world: World) -> None:
    period = await ready(world)
    group_posts = await work_type(world, code="GROUP_POST", name="Bài Group/Chat khách/Order CTV")
    await rule(world, group_posts, minutes=Decimal("30"))
    draft = await self_draft(world, period)
    priced = await add(world, draft.plan.id, group_posts, "28")
    assert priced.workload is not None
    [row] = priced.workload.quotas
    assert row.status is QuotaWorkloadStatus.PRICED
    assert row.target_value == Decimal("28") and row.target_unit_label == "đầu việc"
    assert row.rule_label == "30 phút / đầu việc"
    assert row.contribution_minutes == Decimal("840.00")
    assert priced.workload.projected_minutes == Decimal("840.00")
    # 28 -> 35 is recomputed by the server on the same version: 1 050.
    changed = await world.services.work_plans.update_quota(
        actor=world.actor(world.member),
        request_id=world.request_id,
        plan_id=draft.plan.id,
        quota_id=row.quota_id,
        target_value=Decimal("35"),
        eligibility_cap=Decimal("35"),
    )
    assert changed.workload is not None
    assert changed.workload.quotas[0].contribution_minutes == Decimal("1050.00")
    assert changed.plan.version_no == draft.plan.version_no


async def test_3_4_a_quantity_quota_is_priced_per_unit_never_per_batch(world: World) -> None:
    """120 comments = 120 minutes is entered as 1 per comment; 600 comments are 600."""
    period = await ready(world)
    seeding = await seeding_type(world)
    await rule(world, seeding, minutes=Decimal("1"))
    draft = await self_draft(world, period)
    priced = await add(world, draft.plan.id, seeding, "600")
    assert priced.workload is not None
    [row] = priced.workload.quotas
    assert row.basis is PrWorkQuotaBasis.QUANTITY and row.unit is PrWorkUnit.COMMENT
    assert row.target_unit_label == "bình luận"
    assert row.rule_label == "1 phút / bình luận"
    assert row.contribution_minutes == Decimal("600.00")
    assert row.contribution_minutes != Decimal("72000.00")
    # A fractional per-unit rate is the rule model's own shape: 0.9 a comment.
    fractional = await work_type(
        world,
        code="SEEDING_2",
        name="Seeding 2",
        unit=PrWorkUnit.COMMENT,
        basis=PrWorkQuotaBasis.QUANTITY,
    )
    await rule(world, fractional, minutes=Decimal("0.9"))
    again = await add(world, draft.plan.id, fractional, "100")
    assert again.workload is not None
    by_type = {row.work_type_id: row for row in again.workload.quotas}
    assert by_type[fractional.id].rule_label == "0,9 phút / bình luận"
    assert by_type[fractional.id].contribution_minutes == Decimal("90.00")


async def test_5_6_quotas_sum_and_round_like_m6(world: World) -> None:
    period = await ready(world)
    short = await work_type(world, code="TINY", name="Kịch bản siêu ngắn")
    posts = await work_type(world, code="GROUP", name="Bài Group")
    scripts = await work_type(world, code="SCRIPT", name="Kịch bản video ngắn/Bài đăng")
    odd = await work_type(
        world, code="ODD", name="Lẻ", unit=PrWorkUnit.COMMENT, basis=PrWorkQuotaBasis.QUANTITY
    )
    await rule(world, short, minutes=Decimal("15"))
    await rule(world, posts, minutes=Decimal("30"))
    await rule(world, scripts, minutes=Decimal("40"))
    await rule(world, odd, minutes=Decimal("0.3333"))
    draft = await self_draft(world, period)
    await add(world, draft.plan.id, short, "10")
    await add(world, draft.plan.id, posts, "28")
    await add(world, draft.plan.id, scripts, "30")
    last = await add(world, draft.plan.id, odd, "7")
    workload = last.workload
    assert workload is not None
    contributions = sorted(row.contribution_minutes or Decimal("0") for row in workload.quotas)
    # 7 x 0.3333 = 2.3331, quantized to 2.33 per quota - M6's MINUTES_QUANTUM.
    assert contributions == [
        Decimal("2.33"),
        Decimal("150.00"),
        Decimal("840.00"),
        Decimal("1200.00"),
    ]
    assert workload.projected_minutes == sum(contributions, Decimal("0"))
    assert workload.projected_minutes == Decimal("2192.33")
    assert workload.percent == Decimal("33.2")  # 2192.33 / 6600, to WORKLOAD_QUANTUM
    assert workload.is_complete is True


def test_6b_price_quota_is_pure_and_the_rule_label_is_the_servers() -> None:
    quota = PrWorkQuota(
        id=uuid.uuid4(),
        plan_id=uuid.uuid4(),
        work_type_id=uuid.uuid4(),
        basis=PrWorkQuotaBasis.ITEM_COUNT,
        target_value=Decimal("28"),
        eligibility_cap=Decimal("28"),
        unit=None,
    )
    unpriced = price_quota(quota, None, None)
    assert unpriced.status is QuotaWorkloadStatus.NO_SCORING_RULE
    assert unpriced.contribution_minutes is None and unpriced.rule_label is None
    assert unpriced.status_label == "Chưa cấu hình quy tắc workload"
    assert workload_rule_label(Decimal("30.0000"), PrWorkQuotaBasis.ITEM_COUNT, None) == (
        "30 phút / đầu việc"
    )
    assert workload_rule_label(Decimal("240"), PrWorkQuotaBasis.QUANTITY, PrWorkUnit.SESSION) == (
        "240 phút / buổi"
    )
    assert format_minutes(Decimal("6204.00")) == "6204"
    assert format_minutes(Decimal("0.9000")) == "0,9"


# ===========================================================================
# 2. Incomplete pricing
# ===========================================================================


async def test_7_11_one_unpriced_quota_marks_the_plan_incomplete_and_names_it(
    world: World,
) -> None:
    period = await ready(world)
    priced_types = [
        await work_type(world, code=f"T{index}", name=f"Loại {index}") for index in range(4)
    ]
    for type_row in priced_types:
        await rule(world, type_row, minutes=Decimal("90"))
    missing = await work_type(world, code="NEW", name="Loại chưa có định mức")
    draft = await self_draft(world, period)
    for type_row in priced_types:
        await add(world, draft.plan.id, type_row, "16.5")
    last = await add(world, draft.plan.id, missing, "3")
    workload = last.workload
    assert workload is not None
    assert workload.projected_minutes == Decimal("5940.00")
    assert workload.is_complete is False
    assert workload.priced_quota_count == 4 and workload.unpriced_quota_count == 1
    assert workload.unpriced_work_types == ((missing.id, "Loại chưa có định mức"),)
    # 5 940 / 6 600 would be 90%. It is not printed.
    assert workload.target_minutes == Decimal("6600.00") and workload.percent is None
    body = detail_of(world, draft.plan.id, as_user=world.member)["workload"]
    assert body["percent"] is None and body["is_complete"] is False
    assert body["projected_minutes"] == "5940.00"
    assert body["unpriced_work_types"] == [
        {"work_type_id": str(missing.id), "work_type_name": "Loại chưa có định mức"}
    ]
    gap = next(row for row in body["quotas"] if row["work_type_id"] == str(missing.id))
    assert gap["is_priced"] is False and gap["status"] == "NO_SCORING_RULE"
    assert gap["status_label"] == "Chưa cấu hình quy tắc workload"
    assert gap["contribution_minutes"] is None and gap["rule_label"] is None


async def test_7b_an_excluded_type_is_a_decision_not_a_gap(world: World) -> None:
    period = await ready(world)
    misc = await work_type(world, code="MISC", name="Việc khác")
    await rule(world, misc, minutes=None, mode=PrWorkScoringMode.EXCLUDED_FROM_PERFORMANCE)
    draft = await self_draft(world, period)
    priced = await add(world, draft.plan.id, misc, "5")
    workload = priced.workload
    assert workload is not None
    [row] = workload.quotas
    assert row.status is QuotaWorkloadStatus.EXCLUDED_FROM_PERFORMANCE
    assert row.contribution_minutes == Decimal("0.00")
    assert workload.is_complete is True and workload.excluded_quota_count == 1
    assert workload.percent == Decimal("0.0")


# ===========================================================================
# 3. Target minutes
# ===========================================================================


async def test_12_13_september_2026_is_22_weekdays_times_300(world: World) -> None:
    period = await ready(world)
    type_row = await work_type(world)
    await rule(world, type_row, minutes=Decimal("60"))
    draft = await self_draft(world, period)
    priced = await add(world, draft.plan.id, type_row, "10")
    workload = priced.workload
    assert workload is not None and workload.target is not None
    assert workload.target.calendar_workdays == Decimal("22")
    assert workload.target.eligible_workdays == Decimal("22")
    assert workload.target.daily_target_minutes == 300
    assert workload.target_minutes == Decimal("6600.00")
    assert workload.rules_effective_on == date(2026, 9, 30)
    body = detail_of(world, draft.plan.id, as_user=world.member)["workload"]
    assert body["eligible_workdays"] == "22" and body["daily_target_minutes"] == 300
    assert body["target_minutes"] == "6600.00" and body["target_is_overridden"] is False


async def test_14_no_schedule_means_minutes_but_no_percentage(world: World) -> None:
    await policy(world)
    period = await month(world)
    type_row = await work_type(world)
    await rule(world, type_row, minutes=Decimal("60"))
    draft = await self_draft(world, period)
    priced = await add(world, draft.plan.id, type_row, "99")
    workload = priced.workload
    assert workload is not None
    assert workload.projected_minutes == Decimal("5940.00")
    assert workload.target_minutes is None and workload.percent is None
    assert workload.is_complete is True
    body = detail_of(world, draft.plan.id, as_user=world.member)["workload"]
    assert body["target_unresolved_reason"] == "no_active_work_schedule"
    assert body["target_unresolved_label"] == "Chưa có lịch làm việc đang áp dụng cho kỳ này."
    assert body["percent"] is None and body["projected_minutes"] == "5940.00"


# ===========================================================================
# 4. Approved and draft
# ===========================================================================


async def test_16_20_approved_and_draft_are_two_figures_until_approval(world: World) -> None:
    period = await ready(world)
    type_row = await work_type(world)
    await rule(world, type_row, minutes=Decimal("60"))
    current = await approved_plan(
        world, period=period, quotas=((type_row, Decimal("100"), Decimal("100")),)
    )
    revised = await world.services.work_plans.revise(
        actor=world.actor(world.owner), request_id=world.request_id, plan_id=current.id
    )
    changed = await world.services.work_plans.update_quota(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        plan_id=revised.plan.id,
        quota_id=revised.quotas[0].id,
        target_value=Decimal("120"),
        eligibility_cap=Decimal("120"),
    )
    assert changed.workload is not None and changed.workload.percent == Decimal("109.1")

    [row] = [
        one for one in summary_rows(world, period.id) if one["user_id"] == str(world.member.id)
    ]
    assert row["current_plan_id"] == str(current.id)
    assert row["current_workload"]["projected_minutes"] == "6000.00"
    assert row["current_workload"]["percent"] == "90.9"
    assert row["draft_workload"]["projected_minutes"] == "7200.00"
    assert row["draft_workload"]["percent"] == "109.1"
    # Never merged: neither figure is the sum or the mean of the two.
    assert (
        row["current_workload"]["projected_minutes"] != row["draft_workload"]["projected_minutes"]
    )
    # List rows carry the summary only; the breakdown is the detail's.
    assert row["current_workload"]["quotas"] == [] and row["draft_workload"]["quotas"] == []

    await approve(world, revised.plan.id, by=world.owner)
    [row] = [
        one for one in summary_rows(world, period.id) if one["user_id"] == str(world.member.id)
    ]
    assert row["current_plan_id"] == str(revised.plan.id)
    assert row["current_workload"]["projected_minutes"] == "7200.00"
    assert row["draft_workload"] is None


async def test_36_39_submitted_and_returned_drafts_keep_their_proposed_figure(
    world: World,
) -> None:
    period = await ready(world)
    type_row = await work_type(world)
    await rule(world, type_row, minutes=Decimal("60"))
    draft = await self_draft(world, period)
    await add(world, draft.plan.id, type_row, "50")
    await submit(world, draft.plan.id)
    [row] = [
        one for one in summary_rows(world, period.id) if one["user_id"] == str(world.member.id)
    ]
    assert row["draft_review_state"] == "SUBMITTED"
    assert row["draft_workload"]["projected_minutes"] == "3000.00"
    assert row["draft_workload"]["percent"] == "45.5"
    assert row["current_workload"] is None, "a submitted draft is proposed, not applied"
    # The manager edits the target on the same version; the figure follows.
    quota_id = detail_of(world, draft.plan.id)["quotas"][0]["id"]
    edited = await world.services.work_plans.update_quota(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        plan_id=draft.plan.id,
        quota_id=uuid.UUID(quota_id),
        target_value=Decimal("60"),
        eligibility_cap=Decimal("60"),
    )
    assert edited.plan.version_no == draft.plan.version_no
    assert edited.workload is not None and edited.workload.projected_minutes == Decimal("3600.00")
    await world.services.work_plans.return_for_revision(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        plan_id=draft.plan.id,
        note="Giảm bớt",
    )
    [row] = [
        one for one in summary_rows(world, period.id) if one["user_id"] == str(world.member.id)
    ]
    assert row["draft_review_state"] == "RETURNED"
    assert row["draft_workload"]["projected_minutes"] == "3600.00"
    await submit(world, draft.plan.id)
    [row] = [
        one for one in summary_rows(world, period.id) if one["user_id"] == str(world.member.id)
    ]
    assert row["draft_workload"]["projected_minutes"] == "3600.00"


# ===========================================================================
# 5. Manager list
# ===========================================================================


async def test_21_25_the_list_prices_everyone_in_a_fixed_number_of_statements(
    world: World,
) -> None:
    period = await ready(world)
    types = [await work_type(world, code=f"K{index}", name=f"Loại {index}") for index in range(5)]
    for type_row in types:
        await rule(world, type_row, minutes=Decimal("30"))

    async def staff(count: int) -> list[User]:
        people = [
            User(full_name=f"NV {uuid.uuid4().hex[:6]}", role=Role.EMPLOYEE) for _ in range(count)
        ]
        world.session.add_all(people)
        await world.session.flush()
        for index, person in enumerate(people):
            plan = await approved_plan(
                world,
                period=period,
                user=person,
                quotas=tuple((type_row, Decimal("10"), Decimal("10")) for type_row in types),
            )
            if index % 2 == 0:
                # Every other person also has a revision in flight.
                await world.services.work_plans.revise(
                    actor=world.actor(world.owner), request_id=world.request_id, plan_id=plan.id
                )
        return people

    async def statements() -> tuple[int, list[dict]]:  # type: ignore[type-arg]
        counted: list[str] = []

        def record(*args: object, **kwargs: object) -> None:
            counted.append("x")

        engine = world.session.get_bind().engine
        event.listen(engine, "before_cursor_execute", record)
        try:
            rows = summary_rows(world, period.id)
        finally:
            event.remove(engine, "before_cursor_execute", record)
        return len(counted), rows

    await staff(2)
    few, rows = await statements()
    await staff(20)
    many, rows = await statements()
    assert few == many, f"{few} statements for 2 employees, {many} for 22"

    with_plan = [row for row in rows if row["has_plan"]]
    assert len(with_plan) == 22
    for row in with_plan:
        assert row["current_workload"]["projected_minutes"] == "1500.00"
        assert row["current_workload"]["percent"] == "22.7"
    drafts = [row for row in with_plan if row["latest_draft_id"]]
    assert len(drafts) == 11
    assert all(row["draft_workload"]["projected_minutes"] == "1500.00" for row in drafts)
    # Nobody without a plan is at 0%: there is simply no workload object.
    without = [row for row in rows if not row["has_plan"]]
    assert without and all(
        row["current_workload"] is None and row["draft_workload"] is None for row in without
    )


# ===========================================================================
# 6. Detail
# ===========================================================================


async def test_26_30_the_detail_carries_rule_contribution_mode_and_basis(world: World) -> None:
    period = await ready(world)
    tiny = await work_type(world, code="TINY", name="Kịch bản siêu ngắn")
    seeding = await seeding_type(world)
    missing = await work_type(world, code="NEW", name="Loại mới")
    await rule(world, tiny, minutes=Decimal("15"))
    await rule(world, seeding, minutes=Decimal("1"))
    draft = await self_draft(world, period)
    await add(world, draft.plan.id, tiny, "10")
    await add(world, draft.plan.id, seeding, "600")
    await add(world, draft.plan.id, missing, "2")
    body = detail_of(world, draft.plan.id, as_user=world.member)
    rows = {row["work_type_name"]: row for row in body["workload"]["quotas"]}
    assert rows["Kịch bản siêu ngắn"] == {
        **rows["Kịch bản siêu ngắn"],
        "measurement_mode": "ITEM_COUNT",
        "measurement_mode_label": "Theo số đầu việc",
        "target_value": "10.00",
        "target_unit_label": "đầu việc",
        "standard_minutes_per_unit": "15.0000",
        "rule_label": "15 phút / đầu việc",
        "contribution_minutes": "150.00",
        "is_priced": True,
        "status": "PRICED",
    }
    assert rows["Seeding bình luận"] == {
        **rows["Seeding bình luận"],
        "measurement_mode": "QUANTITY",
        "measurement_mode_label": "Theo số lượng",
        "target_unit_label": "bình luận",
        "rule_label": "1 phút / bình luận",
        "contribution_minutes": "600.00",
    }
    assert rows["Loại mới"]["is_priced"] is False
    assert rows["Loại mới"]["status_label"] == "Chưa cấu hình quy tắc workload"
    assert rows["Kịch bản siêu ngắn"]["rule_version_no"] == 1
    assert rows["Kịch bản siêu ngắn"]["rule_effective_from"] == "2026-01-01"
    # The quota ids line up with the plan's quotas, so a screen can join them.
    assert {row["quota_id"] for row in body["workload"]["quotas"]} == {
        row["id"] for row in body["quotas"]
    }
    assert body["workload"]["rules_effective_on"] == "2026-09-30"


async def test_x_y_the_rule_in_force_on_the_periods_last_day_prices_the_plan(
    world: World,
) -> None:
    """Effective-dated: a rate raised on 1 October does not re-price September."""
    period = await ready(world)
    type_row = await work_type(world)
    await rule(world, type_row, minutes=Decimal("60"), effective_from=date(2026, 1, 1))
    draft = await self_draft(world, period)
    await add(world, draft.plan.id, type_row, "10")
    await rule(world, type_row, minutes=Decimal("600"), effective_from=date(2026, 10, 1))
    body = detail_of(world, draft.plan.id, as_user=world.member)["workload"]
    assert body["quotas"][0]["rule_label"] == "60 phút / đầu việc"
    assert body["quotas"][0]["contribution_minutes"] == "600.00"
    october = await month(world, number=10)
    later = await self_draft(world, october)
    priced = await add(world, later.plan.id, type_row, "10")
    assert priced.workload is not None
    assert priced.workload.quotas[0].standard_minutes_per_unit == Decimal("600.0000")


# ===========================================================================
# 7. Config
# ===========================================================================


async def test_41_44_the_rule_list_carries_its_basis_and_employees_cannot_edit(
    world: World,
) -> None:
    posts = await work_type(world, code="GROUP", name="Bài Group")
    seeding = await seeding_type(world)
    await rule(world, posts, minutes=Decimal("30"))
    await rule(world, seeding, minutes=Decimal("1"))
    world.act_as(world.owner)
    response = world.client.get("/api/pr/performance/scoring-rules")
    assert response.status_code == 200, response.text
    rows = {row["work_type_name"]: row for row in response.json()}
    assert rows["Bài Group"]["rule_label"] == "30 phút / đầu việc"
    assert rows["Bài Group"]["unit_label"] == "đầu việc"
    assert rows["Bài Group"]["measurement_mode"] == "ITEM_COUNT"
    assert rows["Seeding bình luận"]["rule_label"] == "1 phút / bình luận"
    assert rows["Seeding bình luận"]["measurement_mode_label"] == "Theo số lượng"
    assert rows["Seeding bình luận"]["version_no"] == 1
    assert rows["Seeding bình luận"]["effective_from"] == "2026-01-01"
    world.act_as(world.member)
    assert world.client.get("/api/pr/performance/scoring-rules").status_code == 403
    refused = world.client.post(
        "/api/pr/performance/scoring-rules",
        json={
            "work_type_id": str(posts.id),
            "mode": "STANDARD_MINUTES",
            "standard_minutes_per_unit": "1",
            "effective_from": "2026-01-01",
        },
    )
    assert refused.status_code == 403
    with pytest.raises(PrPermissionDeniedError):
        await world.services.work_scoring_rules.create_draft(
            actor=world.actor(world.member),
            request_id=world.request_id,
            work_type_id=posts.id,
            mode=PrWorkScoringMode.STANDARD_MINUTES,
            standard_minutes_per_unit=Decimal("1"),
            effective_from=date(2026, 1, 1),
        )
