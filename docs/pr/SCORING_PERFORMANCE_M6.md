# M6 — Scoring & Performance Engine

**Status: implemented and verified. Deploy candidate. Not deployed.**

> ## M6 scores and reports performance. It does not determine or allocate pay.
>
> ```
> Performance Index  ≠  Bonus  ≠  Salary
> ```
>
> The department head decides and distributes performance money **manually,
> outside MeoChat**, using this report as management evidence. M6 contains no
> coefficient, no amount, no allocation and no pool — not hidden, not disabled,
> **absent**. A performance index is an evaluation result; a schema or a screen
> that placed it beside a multiplier would make it read as a promise about
> somebody's pay, and that is the thing this milestone deliberately does not do.
>
> A future Compensation milestone may introduce pay separately if the business
> ever wants it.
> **Superseded in one rule by [`WORK_RESULTS_BY_PERIOD.md`](WORK_RESULTS_BY_PERIOD.md)
> (migration `0039`).** §4 below says *"only M2's eligible amount is priced"* and
> *"a `NO_QUOTA` row carries a zero eligible amount"*. Neither holds any more:
> M6 prices the **whole counted amount** of every counted contribution in the
> month, with or without a quota, and the KPI target is compared beside it.
> Everything else in this document - the rate model, the target, the review, the
> index, the gate and the bands - is unchanged.

**Migration:** `0035_pr_scoring_performance_engine`, down-revision `0034`. Eight
new tables, nothing existing touched, **no backfill**.

---

## 1. The seven things this milestone keeps apart

> ```
> WORK → COUNTED → ELIGIBLE → STANDARD MINUTES → MANAGER REVIEW
>      → PERFORMANCE INDEX → PERFORMANCE BAND
> ```
>
> **They are not synonyms.** Collapsing any adjacent pair is how a performance
> system stops meaning anything.

M1 answers *did this work happen and did somebody independent confirm it*. M2
answers *is it inside an approved quota*. M6 answers *how much standard work was
that, how did the manager judge the month, and what does the pair come to*. No
step reaches back into the one before it.

### One workload point is one standard minute

> **`1 điểm workload = 1 phút chuẩn.`**

300 standard minutes is a KPI workday; 25 of them is 7500, which is 100% of a
month's capacity. Nothing in the code calls this `score` or `points` — a field
called `points` would invite somebody to add a manager's `105` to an editor's
`90` and get `195` of nothing.

### Quality, timeliness and contribution are ONE review per person per month

> **Not one per work item.** Twenty people is twenty forms a month. The same
> model applied per deliverable would be two thousand — a system nobody fills in,
> and therefore a system that scores nothing.

The unique constraint on `(user_id, reporting_period_id)` makes that
unrepresentable to get wrong.

---

## 2. Canonical rounding — business policy, not presentation

One rounding mode, one place: `ROUND_HALF_UP`, in
`domain/pr/performance.py::quantize`. `ROUND_HALF_EVEN` would send 1.005 and
1.015 in opposite directions — defensible statistically, indefensible to an
employee comparing two payslips.

| Quantity | Places | Why |
| --- | ---: | --- |
| Workload score | **1** | It is a *canonical component*: the figure shown is the figure the index is computed from |
| Manager scores | exact | Policy values — 110 / 105 / 100 / 90 / 85 / 80 / 70 |
| Raw & final index | 2 | |
| Money | 2 | Pool residuals reconcile to the cent |

**The workload decimal is the load-bearing one.** 7820 eligible minutes against a
7500 target is 104.2666…, reported and carried as **104.3**. Carrying more
precision than the figure shown would make the screen's numbers fail to reproduce
the screen's own total — an employee checking by hand would be right and the
system wrong.

### The canonical worked example, reproduced by test

```
eligible 7,820 / target 7,500      → workload  104.3
104.3×.50 + 100×.30 + 105×.10 + 105×.10 → raw PI  103.15
quality 100 → no gate cap          → final PI  103.15
103.15                             → band  Đạt
```

The chain **ends at the band.** There is no next line.

---

## 3. Schema — six tables

| Table | What |
| --- | --- |
| `pr_work_scoring_rules` | What a work type is worth per eligible unit. Versioned, effective-dated, approved |
| `pr_performance_policies` | Weights, caps, barems, gate, bands. Versioned and approved |
| `pr_performance_reviews` | **One row per person per month**, three judgements |
| `pr_performance_target_overrides` | A hand-set workload target and its mandatory reason |
| `pr_work_score_allocations` | One row per scored contribution, naming the rule version that priced it |
| `pr_performance_results` | The monthly figure with every input it came from |

Constraints that carry rules rather than tidiness:

* weights **total exactly 100**, at the database;
* a reviewer is **never** the person reviewed;
* a level and its score are written together or not at all;
* **any rung but Đạt requires a note** — the result moves money;
* a target override **requires a reason**;
* minutes exist exactly when a rule priced them;
* a finalised result **names its policy**, or it cannot be reproduced.

---

## 4. Workload

```
eligible_standard_minutes = M2 eligible_amount × standard_minutes_per_unit
workload_score = eligible / target × 100, capped at 120
```

Identical for `ITEM_COUNT` and `QUANTITY` — the basis lives on the allocation, so
M6 multiplies one number by one rate either way. 120 comments against a cap of
100, at 0.9 min/comment, is **90 minutes and not 108**: the allocation carries
eligible and over-quota as separate columns and only the first is read. **Nothing
in M6 checks for `OVER_QUOTA`** — the portion beyond the cap never enters the sum.

### A missing rate is not zero

| Status | Meaning | Blocks finalisation |
| --- | --- | :---: |
| `SCORED` | A rule applied | — |
| `NO_SCORING_RULE` | **Nobody has configured one.** Configuration, not zero | **yes** |
| `EXCLUDED_FROM_PERFORMANCE` | Somebody deliberately excluded this type | no |

Treating the second as zero would quietly deflate somebody's month because an
owner had not finished setting the system up. `OTHER_OPERATIONAL` is the usual
first `EXCLUDED_FROM_PERFORMANCE`: a fallback heading is real work but not a
measurable job, and giving it minutes would reward filing work under it.

### Rule versioning

`DRAFT → APPROVED → SUPERSEDED`. Approving closes the predecessor the day before
the successor opens; overlapping approved ranges are refused. **The version is
chosen by the contribution's `counted_at`**, so a recalculation in 2027 prices
September 2026 at September's rate.

---

## 5. The target — and why it refuses

```
target = eligible_workdays × daily_target_minutes
```

Built from real data: `work_schedules.working_days`, `organization_holidays`, and
**approved** `hr_requests`.

| Fact | Effect on the target |
| --- | --- |
| Non-working weekday / company holiday | not counted — it was never a workday |
| Approved full-day or multi-day leave | −1 day |
| Approved morning / afternoon leave | −0.5 day |
| Approved hourly leave, late arrival | **nothing** — an hour out of a day is not a day off |
| **Unauthorised absence** | **nothing** — there is no approved row to subtract |

The last row is achieved by reading the data rather than by a rule about it.

**No active work schedule ⇒ `TARGET_UNRESOLVED`, never 7500.** MeoBot already
refuses to compute lateness rather than assume an office opens at 08:00; the same
stance applies here, because a silently-defaulted denominator produces a wrong
index that looks exactly like a right one. An owner may override the target — with
a **mandatory reason**, enforced by a CHECK.

---

## 6. The monthly review

Three dimensions, five rungs, **Đạt = exactly 100** on all three.

| Level | Quality | Timeliness | Contribution |
| --- | ---: | ---: | ---: |
| Xuất sắc | 110 | 110 | 110 |
| Tốt | 105 | 105 | 105 |
| **Đạt** | **100** | **100** | **100** |
| Chưa đạt | 85 | 90 | 90 |
| Không đạt | 70 | 80 | 80 |

A person who did what the role expects is neither rewarded nor punished by the
review terms — which is what makes the workload term mean anything. The barem is
not a disciplinary instrument; the quality floor is 70, not 0.

**System data is evidence, never a score.** The review page shows counted work,
eligible work, over-quota counts, the work-type breakdown and recorded overdue
items — and none of it becomes the answer. The editor whose cut was late because
a doctor moved a shoot is the case that decides this: the system can see the late
task and cannot see the reason.

A missing dimension is **missing**, never defaulted to 100. Partial reviews are an
ordinary state and report `PERFORMANCE_REVIEW_PENDING`.

---

## 7. The index

```
raw = workload×.50 + quality×.30 + timeliness×.10 + contribution×.10
final = min(raw, quality_gate_cap)      ← applied AFTER the raw index
```

**Only quality gates.**

| Quality | Final index capped at |
| --- | --- |
| ≥ 90 | — |
| 80–89.99 | 100 |
| 70–79.99 | 90 |
| < 70 | 80 |

A perfect 120 workload with *Chưa đạt* quality is capped at 100: **volume cannot
rescue bad work.** Timeliness and contribution carry 10% each and no gate — a
hard gate on three dimensions is three ways for one bad judgement to erase a
month.

Bands (`Cần cải thiện` / `Chưa đạt` / `Gần đạt` / `Đạt` / `Vượt kỳ vọng`) are
**a classification of the month, and nothing else**: bands are not mapped to
money anywhere, and M6 has nothing to map them to.

---

## 8. Reports

Two, both rendered from server-computed values.

**Employee, one month.** Target (with the arithmetic that produced it), eligible
standard minutes, workload score, the three ratings with their notes, raw index,
the gate, final index and band — plus the workload breakdown and the deadline
evidence. Read-only, and it ends at the band with one sentence: *"Chỉ số hiệu
suất là căn cứ đánh giá hiệu quả công việc. Chính sách phân bổ thưởng do quản lý
quyết định riêng."*

**Head, one month.** One row per active employee — target, eligible minutes,
workload, the three ratings, final index, band, status — plus a summary header:
headcount, reviewed, pending, finalised, band distribution and averages over the
months that **have** an index. An unreviewed month contributes to no average,
because treating it as zero would report the department as having performed badly
when nobody had got round to reviewing somebody.

The head uses that report as one input to a compensation decision taken
elsewhere. **MeoChat does not record that decision.**

## 9. Permissions

| | Employee | Team Lead (`PR_WORK_MANAGE`) | Admin / Owner |
| --- | :---: | :---: | :---: |
| Read own performance & review | yes | yes | yes |
| Read a colleague's | no | no | yes |
| Configure rates / policy / compensation (`PR_WORK_CONFIGURE`) | no | **no** | yes |
| Review somebody (`PR_PERFORMANCE_REVIEW`) | no | **no** | yes |
| Review **themselves** | **no** | **no** | **no** |

`PR_PERFORMANCE_REVIEW` is new, paired with `user.manage` — already "may make
decisions about people" — so it reaches Admin and Owner and stops short of a
Trưởng nhóm. MeoBot models no team, so there is no honest narrower scope, and
deriving one from channel assignments would make *who may rate you* depend on who
publishes your work.

---

## 10. Period states, finalisation, audit

`OPEN` — preview and recalculate freely, review editable, inputs editable.
`CLOSED` / `LOCKED` — every write refused. **There is no force flag anywhere**, and
a test asserts the absence of the parameter rather than trusting it.

Finalisation runs the **identical calculation** as the preview and refuses with
per-component diagnostics when anything is unresolved — a preview that could
differ from the finalised figure would make every preview a guess.

Sixteen audit actions, one per decision somebody could later be asked about,
including **one per review dimension**: *"who changed my timeliness from Đạt to
Chưa đạt"* is a question about one dimension, and a shared event would make
answering it a diff of a JSON blob. A re-save that changes nothing writes nothing.

---

## 11. What M6 never does

Writes nothing belonging to M1, M2 or M3 — `count_status` is M1's word, quota
allocations are M2's rows, content mappings are M3's. Asserted against the source,
by regex that distinguishes assignment from comparison (the projection
legitimately *filters* on `count_status == COUNTED`).

No points, no scoring beyond the index, no team model, no sales formula, no
per-item quality or timeliness rating, no automatic timeliness from deadlines, no
backfill.

---

## 12. Known limitations

1. **`_project` re-reads allocations per snapshot.** Linear and cached per work
   type per day within a run; a month of 40 contributions costs ~100 ms.
2. **No M2 capacity diagnostic yet** (Part AV) — planned for M6B, where it is a
   screen rather than an engine concern.
4. **A pre-existing M2 test fragility** surfaces when several contributions are
   stamped with `counted_at` under SQLite: re-read rows come back naive while a
   freshly-set value stays aware, and M2's allocation sort compares one of each.
   `test_pr_work_quota.py::test_04` reproduces it standalone with **no M6 code
   involved**, so M6 did not cause it and this milestone does not reopen M2. M6's
   own tests route around it by carrying the amount on one contribution.

---

## 13. M6B — the screens

Four surfaces, all inside the existing *Công việc* shell. Nothing here is a
separate mini-app and nothing here calculates.

```
Công việc
├── Công việc      (M1 ledger)
├── Kế hoạch KPI   (M2)
├── Hiệu suất      ← M6B
└── Cấu hình
    ├── Loại công việc      (M2.5)
    ├── Mapping nội dung    (M3.1)
    ├── Quy tắc workload    ← M6B
    └── Chính sách hiệu suất ← M6B
```

### The rule the frontend obeys

> **The browser calculates nothing.** Workload score, raw index, gate cap, final
> index and band all arrive computed and are rendered.

Enforced by a test that reads the source: `performance.tsx` and `policy.tsx`
contain no `* 0.5`, no `/ 100`, no `Math.min`. The one multiplication in M6B is
in the rule editor — *"0,9 phút × 100 bình luận ≈ 90 phút"*, a hint about the
rate being typed, whose 100 is never sent anywhere — and that file is instead
asserted to contain no performance vocabulary at all.

The reason is M6A's canonical-component rule: the figure a person is shown *is*
the figure their money was computed from. A browser that re-derived
`104,3 × 0,50` would eventually disagree with the server by a hundredth, and the
person being evaluated would be told two different true things.

### Quy tắc workload

Table of rate versions per work type: *Loại công việc · Cách đo · Phút chuẩn /
đơn vị · Hiệu lực · Trạng thái*. A `DRAFT` offers **Duyệt** behind a dialog that
says the rate becomes immutable; an `APPROVED` row offers **Tạo bản điều chỉnh**
and — deliberately — **no edit button**, because offering a control the server
refuses is how somebody learns the screen is guessing. Superseded versions stay
listed with their closed date range.

### Chính sách hiệu suất

The active policy's weights, caps, **all three barems and the quality gate,
rendered from the policy row**. Not one score is written in the file: a
department that decides *Tốt* is worth 108 changes one version and every screen
follows. The gate is presented as the sentence it is — *"chất lượng là điều kiện
giới hạn hiệu suất"* — with the policy's own thresholds beneath it, and an
explicit note that timeliness and contribution have **no** gate.

### Hiệu suất — the monthly table

One row per employee: target, workload, the three ratings, index, band,
status. Status is translated into something a person can act on —
*Chờ đánh giá*, *Chưa xác định mục tiêu*, *Thiếu quy tắc workload*, *Sẵn sàng
chốt*, *Đã chốt* — and never into "Lỗi".

**An unrated dimension reads *Chưa đánh giá*, never 0.** Rendering an absent
number as zero renders somebody as having performed badly.

### The review form

Three dimensions, one form, one month. Each carries the policy's rungs, an
expandable rubric, and a note field that becomes mandatory the moment the rung is
not *Đạt* — mirrored from the server, which enforces it regardless.

Above it, **Dữ liệu hệ thống**: the target arithmetic as a sum a person can check
(*ngày làm việc − nghỉ phép × 300*), the eligible minutes, the work-type
breakdown, and the deadline figures under **Dữ liệu tham khảo về tiến độ —
không phải điểm tiến độ**. Nothing preselects a rating from that evidence: the
system can see a cut was late and cannot see that a doctor moved the shoot.

### The result, and the gate

Components, raw index, gate, final index and band — all rendered.
When a gate applies the screen says so in words: *"Khối lượng cao không bù được
cho chất lượng dưới chuẩn."* An unexplained cap is exactly what makes the
question feel arbitrary.

### Target override and finalisation

A `TARGET_UNRESOLVED` month offers a manual target whose **reason field is
mandatory** — the one figure in M6 a person types rather than the system
computing it. There is no money surface of any kind.

Finalisation asks first and, when blocked, shows a **checklist naming the missing
thing** — including which work type has no rate and which dimension is unrated —
rather than "finalisation failed". A finalised month, and any `CLOSED` or
`LOCKED` period, renders every control disabled.

### The employee's view

The same evidence and result, read-only, with the manager's notes and the
sentence that keeps an index from reading as a salary multiplier. No form, no
colleagues, no self-rating, no money.

## 14. Precision on screen

Locale formatting only; **no second quantization**. `103.15` renders `103,15`. A separate `formatRate` trims trailing zeros for
rates, weights, caps and ratings — `90.0000` is *90 phút* — because trimming
zeros after the decimal point cannot change what a number means. It is deliberately **not** used for the
index, whose two places are part of the canonical figure.

## 15. M6B's backend additions

Three **read-only** additions, no calculation changed:

1. `is_finalized` was declared in M6A and never populated — the snapshot never
   read the result row, so a finalised month reported `false` and the screen
   would have offered controls the server refuses. Now populated, with
   `finalized_at` and `finalized_by_user_id`;
2. `planned_standard_minutes` — **Part AV**, the KPI plan's approved caps priced
   at the same rates the workload uses, so a plan worth 69% of the month is
   visible in week one. Diagnostic only: it writes no quota, and a work type with
   no rate is left out rather than guessed;
3. nothing else. The arithmetic module is untouched, asserted by re-running the
   canonical example through it.

## 16. M7 handoff

M7 may add trends, comparison, workload balance, bonus analysis, forecasting and
outlier detection. **None of it is here**: this milestone ships one month at a
time, and the monthly table is a worklist rather than a dashboard. What M7
inherits is a result row per person per month carrying every input it was
computed from — which is what makes a trend line explainable rather than merely
drawable.
