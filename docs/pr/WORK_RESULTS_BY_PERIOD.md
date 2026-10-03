# Recurring work records actual output by period — KPI is a target only

**Status: implemented, tests green. Not deployed.**
**Migration:** `0039_pr_work_results`, `down_revision = 0038`.

> ```
> WORK = actual work performed.   KPI = target only.
> ```
>
> A KPI target never decides whether work may be reported, how much of it counts,
> or what it is worth. It is compared beside the actual, and that is all it does.

`PLAN.md` at the repository root is the audit and the design; this document is
the reference for what shipped.

---

## 1. The three things that were wrong

| Before | Rule violated |
| --- | --- |
| M6 priced M2's **eligible** amount. 27 customers against a target of 20 earned 20 × rate; work with no approved plan earned nothing | *Do not cap points at KPI. Work without a KPI must score normally* |
| A recurring template created **one work item per firing**, each with a fixed quantity to complete and validate. Content created **one work item per content item** | *One work item per employee + work type + period; results accumulate* |
| `default_unit` was locked once a type was used, and `ITEM` read as *"sản phẩm"* | *The unit must be editable; "Tìm khách hàng" reads "khách hàng"* |

## 2. The period container

An ordinary `PrWorkItem` with two new columns:

```
reporting_period_id  -> pr_reporting_periods     null = one-off job, exactly as M1 shipped it
subject_user_id      -> users                    the one employee the stream belongs to
UNIQUE (work_type_id, reporting_period_id, subject_user_id) WHERE reporting_period_id IS NOT NULL
CHECK  (reporting_period_id IS NULL) = (subject_user_id IS NULL)
```

*Tìm khách hàng — 2026-09* is one row. Its `quantity` is **derived** — the sum of
the results a validator counted — and its single `PRIMARY` contribution is
`COUNTED` while at least one result is, with `counted_at` the earliest result's
validation instant. That is what lets **M2 and M6 read a container through the
same contribution row they already read**.

| Fact | One-off job | Period container |
| --- | --- | --- |
| `quantity` | fixed at creation | `SUM(counted results)`; zero allowed |
| status | `PROPOSED → … → APPROVED` | `ACCEPTED` / `IN_PROGRESS` while the month is open; reads *"Đang ghi nhận kết quả"* |
| `start` / `complete` / `reopen` / `approve` / contributors | as M1 | refused (`reason = period_container`) |
| `cancel` | as M1 | only while it holds no result (`container_has_results`) |
| month attribution | `counted_at` | **its own period**, whatever day the first result was validated |
| `execution_at` | source-dependent | local midnight of the month's first day |

**Who opens one.** All three paths are a get-or-create against the unique index
(`PrWorkResultService.ensure_container`): a routine with *Tích lũy kết quả theo
kỳ* on activation and on each firing; a person reporting the first result of the
month (an employee into their own stream, a manager into anybody's); the content
projector when it has a result to file. A missing month row is opened through
the same code `ensure_month_period` uses, without the configuration capability —
a stream cannot be refused because an administrator has not pressed a button.

## 3. Results

`pr_work_results` — one row per declaration:

```
work_item_id, user_id (= subject), quantity > 0, label?, link?, note?
source_type  MANUAL | CONTENT | SEEDING | CRM | SYSTEM | OTHER
source_key   content:{uuid}:CONTENT_CREATION — null for MANUAL
status       PENDING | COUNTED | EXCLUDED      (M1's own vocabulary)
exclusion_kind  VALIDATOR_REJECTED | ADMIN_REMOVED | SOURCE_REVERSED | null   (0041: why it is out)
reported_by / reported_at, counted_by / counted_at, excluded_by / excluded_at / excluded_reason
UNIQUE (source_type, source_key) WHERE source_key IS NOT NULL
CHECK (status = 'COUNTED') = (counted_at IS NOT NULL)
CHECK status = 'EXCLUDED' OR exclusion_kind IS NULL
```

**M1's boundary, at result grain.** Declaring is not being credited:

| Act | Who | Result |
| --- | --- | --- |
| report (`quantity`, `label`, `link`) | subject with `PR_WORK_EXECUTE`; anybody's with `PR_WORK_MANAGE` | `PENDING` |
| validate (all pending, or a list) | `PR_WORK_VALIDATE`, **not the subject** | `COUNTED`, actual moves |
| reject (*Từ chối / Không ghi nhận*), reason required | `PR_WORK_VALIDATE`, not the subject | `EXCLUDED / VALIDATOR_REJECTED`, row kept, actual drops; **no projection restores it** |
| reconsider (*Xem xét lại*) | `PR_WORK_VALIDATE`, not the subject | `VALIDATOR_REJECTED` → `PENDING`; the rejection stays in history |
| admin remove (*Xóa kết quả*) | `PR_WORK_CONFIGURE` | `EXCLUDED / ADMIN_REMOVED`; the next projection re-evaluates it. Refused on a rejected row |
| withdraw | the reporter, own `MANUAL` `PENDING` result only | row removed, history row kept |
| source result | the projector; counted when the source's validator is not the subject | `COUNTED` on the source's instant, else `PENDING` |

`actual_quantity = SUM(COUNTED)`; `declared_quantity = SUM(PENDING + COUNTED)` is
shown beside it. **Nothing about a target is consulted on any of these paths.**

**The content delete guard reads results too.** `PrLifecycleService._has_source_work`
matches `content:{id}:%` over `pr_work_results` as well as `pr_work_items`, and
refuses with `PrContentHasRecordedWorkError` (`pr_content_delete_blocked_recorded_work`,
HTTP 409) before anything is written. `may_delete` reads that refusal as "no", so
the action list never offers a delete the write would refuse.

## 4. The comparison

`domain/pr/work_results.py::compare_to_target(actual, target)` is the only place
`actual / target` is computed, and it is read by the work card, the KPI summary
(`QuotaTypeProgress.comparison`) and the M6 breakdown:

```
completion_percent = actual / target × 100     uncapped; None when there is no target
over_target        = max(actual − target, 0)
remaining          = max(target − actual, 0)
```

The target is `PrWorkQuota.target_value` inside the approved plan — the existing
`PerformanceTarget` layer (self-service submission or manager creation, versioned,
approved, never self-approved). `eligibility_cap` and M2's
`ELIGIBLE / PARTIALLY_ELIGIBLE / OVER_QUOTA` split are **kept as an
informational decomposition** the KPI screen still shows. Nothing downstream is
limited by them.

## 5. Points

`PrPerformanceService._project` is now driven by **`COUNTED` contributions in the
month** (M1), left-joined to their M2 allocation, and prices the **counted
amount** — `allocation.basis_amount` when M2 measured it, otherwise the same
`measure_contribution` M2 uses. `NO_QUOTA` rows and rows with no allocation at
all (no plan ever approved) are priced like any other.

```
27 khách hàng × 380 phút chuẩn = 10 260      never 20 × 380 = 7 600
```

`price_amount` (rate × amount, `ROUND_HALF_UP`) is unchanged and public; the
work card prices a stream's actual through it at the same `rule_for(type, on=…)`
lookup — the rate in force on the day the stream was first counted, else the
month's last day. `PrWorkScoreAllocation.counted_amount` records what was priced
beside M2's `eligible_amount`. The policy's 120 % `workload_score_cap` still
caps the **index**; it is not a cap on points.

"Counted in this month" is one predicate, `counted_in_period`, shared by M2's
evaluator and summary, M6's projection and evidence, and the Work page's tiles:
a container belongs to its period; a one-off job to the month containing its
`counted_at`.

## 6. Content mapping

Per milestone the projector now:

1. converges a **legacy** work item (created before `0039`) exactly as M3.1 did —
   nothing is migrated, history stays readable and countable;
2. otherwise records **one result** (`CONTENT`, `content:{id}:KIND`, quantity 1,
   label `CNT-… · title`) on the contributor's container for the month of the
   validation instant. Independent validation → `COUNTED` on the source's
   instant; self-approval → `PENDING`; withdrawal → `EXCLUDED`; redo → the same
   row back to `COUNTED`. A replay, a retry and a reconcile find the row through
   the unique index and change nothing;
3. re-files an **uncounted** result onto the container of the corrected type
   when the mapping or the content type changes. A counted one stays where its
   month reported it.

23 approved scripts are one *Kịch bản video ngắn — 2026-09* with 23 results:
`23 / 20 · 115 % · +3`.

## 7. Routines

`pr_work_recurring_templates.accumulate_by_period` (default `false`):

| | `false` — M4B as shipped | `true` — *Tích lũy kết quả theo kỳ* |
| --- | --- | --- |
| each firing | one job per assignee with the template's `quantity` | ensures the month's container per assignee; `work_item_count` = how many were **new** |
| activation | sets the cursor | also opens the current month's containers at once |
| `quantity` | required for `QUANTITY` types | ignored — what a stream should reach is a KPI target, in the plan |
| `assignment_mode` | either | `SEPARATE_PER_ASSIGNEE` only |

The scheduler ledger — cursor, pause, catch-up, closed-period skip, idempotency
key — is untouched.

## 8. Period rollover

There is no close event in the system (`PrReportingPeriod.status` is reserved;
nothing writes `CLOSED`). Rollover is structural: October's stream is a
different row from September's, opened on first use at zero, and September's row,
results, contribution and allocations stay exactly as they were. What already
freezes a month's figures is relied on rather than duplicated — the approved
plan version, the effective-dated rate, the unit copied onto every row, the
result rows, and `PrPerformanceResult` once finalised.

## 9. The unit

`default_unit` left the structural lock. Editing it on a used type updates the
type, the `unit` on that type's containers in **current** open months and on
quotas of **draft** plans. Approved quotas, one-off jobs and containers in past
months keep the unit they were written with. The M2 evaluator no longer treats a
unit label difference as `UNIT_MISMATCH` — both sides are copies of the type's
unit and a renamed label is the same measure. New units: `CUSTOMER` *khách hàng*,
`SCRIPT` *kịch bản*, `ORDER` *đơn hàng*. The migration sets `CUSTOMER` on types
recognisably *Tìm khách hàng*; *Cấu hình* reaches any other.

## 10. API

| Route | Capability | What |
| --- | --- | --- |
| `POST /api/pr/work/results` | `PR_WORK_EXECUTE` (own) / `PR_WORK_MANAGE` | report into the month's stream for a work type; opens it on first use |
| `POST /api/pr/work/{id}/results` | same | report into a named stream |
| `POST /api/pr/work/{id}/results/validate` | `PR_WORK_VALIDATE`, not the subject | count pending results |
| `POST /api/pr/work/results/{id}/exclude` | `PR_WORK_VALIDATE`, not the subject | reject one, with a reason (`VALIDATOR_REJECTED`) |
| `POST /api/pr/work/results/{id}/reconsider` | `PR_WORK_VALIDATE`, not the subject | release a rejection back to pending |
| `DELETE /api/pr/work/results/{id}` | the reporter | withdraw own pending manual result |
| `GET /api/pr/work` | — | `scope` optional: omitted → *Toàn bộ* for `PR_WORK_VIEW_ALL`, else *Của tôi*; rows carry `period_container` |
| `GET /api/pr/work/{id}` | — | `results[]`, `is_subject`, `can_report_result`, `can_validate_results` |

`PeriodContainerResponse` carries `actual_quantity`, `declared_quantity`,
`target_quantity`, `completion_percent` (uncapped), `over_target_quantity`,
`progress_percent` (capped, for the bar), `actual_label` (*"27 / 20 khách hàng"*),
`standard_minutes`. `QuotaTypeProgressResponse` and `WorkTypeBreakdownResponse`
gained `completion_percent` / `over_target_amount`; the breakdown also carries
`counted_amount`.

## 11. Screens

* **Công việc** — *Báo cáo kết quả* beside the create buttons; a stream card
  reads *Thực tế 27 / 20 khách hàng · 135% · +7 vượt chỉ tiêu · 10.260 điểm* with
  a bar that stops at full while the number does not; the detail lists results,
  offers the report form, *Xác nhận N kết quả chờ*, *Loại bỏ* with a reason and
  *Rút lại*. Default scope *Toàn bộ* for anybody who may see it.
* **Giao công việc → Định kỳ** — *Tích lũy kết quả theo kỳ*; quantity hidden
  when on.
* **Cấu hình → Loại công việc** — the unit is editable for every type and shown
  for both bases; three new units.
* **Kế hoạch KPI** — each quota row: *Thực tế 27 / 20 · 135% · +7 vượt chỉ tiêu*.
* **Hiệu suất** — *Workload thực tế*; the breakdown shows the counted amount
  and the comparison.

## 12. Migration and rollback

`0039` is additive: two columns, one CHECK swap, one partial unique index, one
table, one boolean, one nullable numeric with a copy backfill, one bounded data
update. `downgrade` drops all of it, restores the original CHECK, nulls the
derived zero on containers and folds `CUSTOMER / SCRIPT / ORDER` back to `ITEM`
wherever a unit is stored. **Results are lost on downgrade** — dump first.
Roundtrip, drift and the data step are asserted on PostgreSQL 17 in
`tests/integration/test_pr_work_results_pg.py`.

## 13. Not changed

M1's ladder for one-off work; M2's `allocate`, `measure_contribution`,
`candidate_sort_key`, plan lifecycle and self-service; M6's index formula, gate,
bands and target resolution; `PrPlanWorkloadCalculator`; the M4B scheduler;
capabilities and their holders.

## 14. Known limitations and follow-ups

1. A stream is priced at the rate in force on the day its **first** result was
   counted. A mid-month rate change prices the whole month at the earlier rate.
2. No in-app notification is sent for a pending result or a validation.
3. Pending results are validated per stream; there is no cross-stream bulk
   validation.
4. The data step recognises *Tìm khách hàng* by code (`CUSTOMER`, `KHACH`) or
   name ("khách hàng"); any other spelling is corrected in *Cấu hình*.
5. Legacy content work items keep converging as items; only content without a
   pre-existing item flows into results.
