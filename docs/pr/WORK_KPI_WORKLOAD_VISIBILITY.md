# KPI workload visibility

Plan workload, per-quota workload rule and contribution, on every KPI screen.
**No migration.** Alembic head stays at `0038_pr_kpi_plan_submission_review`.

## 1. Audit

| Question | Finding |
| --- | --- |
| Canonical plan-workload calculator before this patch | `PrWorkPlanService._workload_for` (KPI self-service) - `sum(target_value × standard_minutes_per_unit)` over quotas whose type has an approved `STANDARD_MINUTES` rule in force on `period.date_end`, via `PrWorkScoringRuleService.rule_for`. Per quota, per plan, per employee: N+1 on the manager list. |
| M6's own reader | `PrPerformanceService._planned_minutes` sums `eligibility_cap × rate` for the approved plan (capacity, not target) and `_price` multiplies an **eligible amount** by `standard_minutes_per_unit` per contribution. Both untouched. |
| Canonical target-minute resolver | `PrPerformanceTargetService.resolve`: an owner override wins outright; else active `WorkSchedule` working weekdays in the period minus `OrganizationHoliday` rows, minus approved leave fractions per person, × the approved policy's `daily_target_minutes` (default 300). `None` with `unresolved_reason` when there is no active schedule. |
| Why September 2026 is 6 600, not 7 500 | September 2026 has 22 Monday-to-Friday days. 22 × 300 = 6 600. 7 500 is 25 × 300, a nominal figure this system never hard-codes; a month's 100% is its own calendar. A configured 2 September holiday would make it 21 × 300 = 6 300. Nothing in the browser holds either number. |
| Rule model | `pr_work_scoring_rules`: `work_type_id`, `version_no`, `mode` (`STANDARD_MINUTES` / `EXCLUDED_FROM_PERFORMANCE`), `standard_minutes_per_unit` (`Numeric(10,4)`, nullable exactly when excluded), `effective_from`, `effective_to`, `status`. Versioned and effective-dated; approved versions are immutable. **There is no `basis_quantity` column.** |
| What a rule's number means | Minutes per **one quota unit**. For an `ITEM_COUNT` work type that is one work item ("đầu việc"); for a `QUANTITY` type it is one of the type's `default_unit` (a comment, a post, an account). This is exactly what M6 multiplies an eligible amount by, and the module docstring on the model gives "seeding is 0.9 minutes a comment" as the worked example. |

### The QUANTITY question (Part E)

The model cannot say "120 comments = 120 minutes" as a pair; it says "1.0000 minute per comment", and the calculation for 600 comments is 600 × 1 = 600, never 600 × 120. Exact per-batch pricing is therefore representable whenever the per-unit rate is (N minutes ÷ M units to four decimals). That is the canonical semantics and it is what this patch exposes; no `basis_quantity` was invented and no migration was needed.

**What this means for the production configuration** listed in the request ("120 Comment seeding: 120 phút"): the number is correct only if that work type measures by `ITEM_COUNT`, one item being a 120-comment batch. If the type measures by `QUANTITY` in comments, the rule as stored prices each comment at 120 minutes and must be re-entered by an authorised configurer as `1` per comment. The config screen now prints the basis beside every rule ("120 phút / đầu việc" versus "120 phút / bình luận") precisely so a manager can see which of the two it is. This repository has no access to the production database, so the check is listed under *Required before deploy*.

## 2. One calculator

`src/meobot/application/pr_plan_workload.py`:

* `price_quota(quota, rule, work_type) → QuotaWorkload` - **the** target-to-minutes step. Pure. `contribution_minutes = quantize(target_value × standard_minutes_per_unit, 0.01)`; `0` for an excluded rule; `None` and `NO_SCORING_RULE` for no rule.
* `PrPlanWorkloadCalculator.for_plans(plans, period)` - any number of plans for one period in a fixed number of statements: quotas (1), work types (1), rules in force (1, `PrWorkScoringRuleService.rules_for(type_ids, on=period.date_end)`), policy (1), targets (4, `PrPerformanceTargetService.resolve_many`). `for_plan` is the one-plan form.
* `PlanWorkload`: `projected_minutes` (sum of priced contributions), `target_minutes`, `percent` (only when the target resolved **and** `is_complete`), `quotas`, `priced/unpriced/excluded_quota_count`, `unpriced_work_types`, `rules_effective_on`, `target` (the full `TargetResolution`).

`PrWorkPlanService._workload_for` now delegates to it, and `period_summary` prices every current plan and draft in one batched call. `rule_for` delegates to `rules_for`, `resolve` to `resolve_many`, so the single-row and batched paths share one predicate each.

Rounding: minutes to `0.01` per quota (M6's `MINUTES_QUANTUM`) then summed, so the printed rows add up to the printed total; percent to `0.1` (`WORKLOAD_QUANTUM`). Decimal end to end; the wire carries strings; the browser formats and never computes.

Rule date: the period's **last day**, for approved plans and drafts alike - the instant M6 prices end-of-month work at, and the date the self-service preview already used. A rate raised on 1 October does not re-price September (tested).

## 3. Read models

`PlanWorkloadResponse` (in `EmployeePlanSummaryResponse.current_workload` / `draft_workload` and `WorkPlanDetailResponse.workload`) gained: `priced_quota_count`, `unpriced_quota_count`, `excluded_quota_count`, `is_complete`, `unpriced_work_types[{work_type_id, work_type_name}]`, `target_unresolved_reason` + `_label`, `calendar_workdays`, `approved_leave_days`, `eligible_workdays`, `daily_target_minutes`, `target_is_overridden`, `rules_effective_on`, `quotas[]`. The existing four fields are unchanged.

`QuotaWorkloadResponse` (one per quota, **detail responses only**): `quota_id`, `work_type_*`, `measurement_mode` + `_label`, `target_value`, `target_unit_label`, `standard_minutes_per_unit`, `rule_label` ("30 phút / đầu việc"), `rule_version_no`, `rule_effective_from`, `contribution_minutes`, `is_priced`, `status` (`PRICED` / `NO_SCORING_RULE` / `EXCLUDED_FROM_PERFORMANCE`), `status_label`.

List rows carry the summary with `quotas: []`; the breakdown is the detail's. 22 employees: 11 SQL statements, 38 KB.

`WorkScoringRuleResponse` gained `measurement_mode`, `measurement_mode_label`, `unit_label`, `rule_label` from the joined work type.

Labels are the server's: `domain/pr/work_quota_labels.py` - `quota_target_unit_label`, `workload_rule_label`, `format_minutes`, `quota_workload_status_label`, `target_unresolved_label`.

## 4. Screens

`frontend/src/app/pr/work/workload.tsx` is the one place the figure is drawn: `WorkloadSummary` (applied / projected / proposed; compact for list rows), `QuotaWorkloadLine`, `QuotaWorkloadBreakdown`. Every number is a response field.

* **Manager list**: every row prints "Tải KPI 6.204 / 6.600 phút · 94%" from the batched list response; a draft prints "Tải dự kiến" or, when submitted, "Tải đề xuất" on its own line. Never added, never averaged. No plan → "Chưa có kế hoạch", no percentage.
* **Incomplete**: "Tải đã tính 5.940 phút · 1 chỉ tiêu chưa quy đổi · Chưa thể tính chính xác % tải" and the unpriced type named; the quota row shows "⚠ Chưa cấu hình quy tắc workload" and no number.
* **No target**: the minutes and the server's sentence ("Chưa có lịch làm việc đang áp dụng cho kỳ này."). Never 0% or NaN%.
* **Editor** (employee and manager, same component): per quota "28 đầu việc · 30 phút/đầu việc → 840 phút"; the plan's figure under the quotas, so *Gửi duyệt* and *Duyệt và áp dụng* are pressed with it in view. A target edit is saved and the server's recomputed detail is rendered - there is no client-side preview and no local formula.
* **Employee card**: the plan in force shows "Tải KPI" and an on-demand "Chi tiết tải KPI" breakdown.
* **Review**: the proposal panel shows "Tải đề xuất" beside *Duyệt và áp dụng*; expanding it shows the breakdown. Informational only.
* **Config**: the rule column prints "30 phút / đầu việc" / "1 phút / bình luận" and "1 bình luận = 1 phút chuẩn"; the form states the basis of the chosen type before a number is typed.
* **Bands**: none. The product has bands for M6 scores only; a planned-workload band would be a second, unapproved scale. The bar under a figure is a picture of the two numbers.
* **Mobile**: figures stack; the quota breakdown is a vertical list.

## 5. Unchanged

M2 allocation and eligibility, M6 formulas and `_planned_minutes`, Work counting, KPI approval rules and readiness blockers (missing pricing still does not block submission), scoring-rule permissions (`PR_WORK_CONFIGURE`; employees get 403), the 1 point = 1 standard minute definition, Membership & Permissions Phase 1.

## 6. Follow-ups

* **Sort / filter by workload** on the manager list: the batched read model makes it trivial - every row already carries `percent` - but it was not added, to keep this patch to display.
* **Production rule check** for QUANTITY-measured types (see §1).
