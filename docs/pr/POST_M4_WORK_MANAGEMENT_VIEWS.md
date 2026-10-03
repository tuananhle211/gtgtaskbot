# Post-M4 — Employee-centric KPI, one monthly Work ledger, real execution dates

**Status: complete. Nothing deployed.** Migration `0037_pr_work_execution_at`,
`down_revision = 0036`.

> This patch changes **read models and screens**. It changes no M1 lifecycle, no
> M2 eligibility rule, no M3 workload milestone, no M4B scheduler and no M6
> formula.

---

## The three things that were wrong

### 1. KPI listed plan versions, not employees

`GET /api/pr/work/plans` returns `PrWorkPlan` rows and the screen rendered one
card each. An employee on their third revision was **three management
entities**, two of which were history — and an employee with **no** plan was no
row at all, when *"who has no KPI plan this month"* is half of what a manager
opens the screen to find out.

### 2. Work defaulted to today, and read as three ledgers

The outer scope of a management view is a **reporting month**. Asking "what is
due today" and trying to reconstruct September from it is the wrong query in the
wrong order. And a department's September is one thing: content, manual and
recurring work are one list with a source badge, not three boards.

### 3. Dates were invented

A card printed a deadline, or the row's birthday, under a heading claiming the
work was performed.

---

## `execution_at` — one fact, one column

**Every timestamp M1 already had answers a different question:**

| Column | Answers |
|---|---|
| `created_at` | when the row was written |
| `due_at` | when it must be finished |
| `accepted_at` | when it entered a workload |
| `completed_at` | when somebody said it was finished |
| `counted_at` | when it was independently validated |

None is *"on what day was this done"*.

**Why not derive it.** `completed_at` for content work *is* the right instant —
and **`reopen` sets it to null**. A validator sending work back would erase the
day the writer delivered, and a card that had been saying "Thực hiện 04/09"
would silently start saying nothing. A fact a lifecycle action can destroy is
not one a read model can rest on.

### Who writes it

| Source | Execution date | Written by |
|---|---|---|
| `CONTENT` | M3.1's canonical milestone — the head's approval decision for a script, the hand-in of a cut for production | `create_source_work(occurred_at=…)`, which the projector already computed |
| `RECURRING` | the occurrence's `scheduled_for` | `generate_recurring_work` |
| `MANUAL` | **null** | nothing |

Manual work has no execution-date concept: a person filing a job states a
deadline and never says which day they will do it. The screen renders **nothing**
under that heading rather than borrowing `due_at`.

### `recurring_occurrence_id`

The second column in `0037`, and it exists because the alternative was **parsing
a source key**. M4B's key is `recurring:{occurrence-uuid}:{SUBJECT}` and the
module's rule is that a key is compared for equality and never taken apart. A
work card has to offer *"đi tới công việc định kỳ"*; the occurrence ledger
already links occurrence to template, so the missing edge is work → occurrence,
and it belongs in a foreign key rather than in a string somebody decodes.

### The backfill

`0037` is the one migration here that touches existing rows, and it **computes
nothing**: content rows take `completed_at` and recurring rows take
`accepted_at` — the columns those same facts were written to. `recurring_occurrence_id`
is *not* backfilled, because recovering it would mean decoding the key the
column exists to avoid.

---

## The unified monthly Work view

> **Superseded in part.** The post-M4 UX consolidation removed *Định kỳ* as a
> top-level tab; it survives here as a source filter and a badge, and routines
> are created and managed inside *Giao công việc*. See
> [`POST_M4_UNIFIED_WORK_ASSIGNMENT.md`](POST_M4_UNIFIED_WORK_ASSIGNMENT.md).
> Nothing about the read model below changed.

```
period_id                    ← the outer boundary, applied first
   └── source filter         ← Tất cả / Thủ công / Định kỳ / Nội dung
       └── person filter     ← within the caller's existing read scope
           └── status filter ← lifecycle only
               └── day slice ← Tất cả tháng / Hôm nay / Hôm qua / Tuần này / …
```

**Cancelled work is out of the default.** `status` unset means *every status
except `CANCELLED`* — in the page, the total, the summary tiles and a search —
and `status=CANCELLED` (*Đã hủy*, the last option of the status filter) is the
only way a cancelled row is listed. Decided in `PrWorkQueryService._conditions`,
never in the browser, so no client pages, counts or shows a row the dashboard
does not mean. Cancelling still deletes nothing; an administrator may delete a
cancelled row from its detail when it is safe — see
[`WORK_MAINTENANCE.md`](WORK_MAINTENANCE.md) §3d.

**Month first.** `WorkQuery.period_id` is resolved through
`PrWorkPeriodService.bounds` — the one place a reporting period becomes a pair of
Vietnamese calendar instants — and applied before any preset.

**`work_period_instant()`** decides which month a row is listed under:
`coalesce(execution_at, accepted_at, created_at)`. Each fallback is a step down
in confidence, and it is deliberately **not** the same thing as `execution_at`:
this expression decides *listing*, `execution_at` alone is what a screen may
print. Bounding the month on `execution_at` alone would have made every manually
assigned job invisible on the screen built to show the department's month.

**`due_at` is deliberately not in that list** — see the docstring on
`pr_work_query_service.work_period_instant`, which the code has always matched
and an earlier revision of this paragraph did not. A deadline is neither an
assignment date nor an execution date: work assigned on 30 September and due on
2 October is September's work, and a month attributed by deadline would move it
into October while nobody moved anything.

**Ordering** is `execution_at ASC NULLS LAST`, then deadline, then id. Undated
work is the tail under *"Chưa có ngày thực hiện"* and is **never hidden** —
giving it a `created_at` to make it sortable is the invention this removes.

**The summary tiles count the whole month** and do not move with the day slice.
That is stated on the screen (*"Tính cho cả kỳ 2026-09 · không đổi theo bộ lọc
ngày bên dưới"*), because two kinds of number in one row with nothing
distinguishing them is the mismatch to avoid. The selected period also **wins
over the preset bounds** in the summary — otherwise a manager looking at August
in September would have read zero.

---

## The employee-centric KPI read model

`GET /api/pr/work/plans/summary?period_id=` — **one row per active employee**,
built from the directory and having the plans attached, so somebody with no plan
still appears.

`current_plan_of()` decides precedence, and it is short because M2's schema
already did the work: partial unique indexes allow at most one `APPROVED` and at
most one `DRAFT` per `(user, period)`.

1. `APPROVED` — in force, and what eligibility reads;
2. else `DRAFT` — somebody is writing this month's plan;
3. else nothing. `SUPERSEDED` and `DISCARDED` are **never** current.

`latest_draft_id` is reported separately, because "open the plan in force" and
"continue the revision somebody started" are two different controls.

`GET /api/pr/work/plans/history?user_id=&period_id=` — every version, **ordered
by `version_no` descending**, the one key that cannot tie.

### Three sections, and a draft is not one of the other two

The detail screen has **three** parts, and they are not peers:

| Section | What it is | Where it comes from |
|---|---|---|
| **Kế hoạch hiện tại** | the version in force — `APPROVED`, else the initial `DRAFT` | `current_plan_of()`, `is_current` |
| **Bản điều chỉnh đang soạn** | the revision being written, when it is a *different* row | `active_draft_of()`, `is_active_draft` |
| **Lịch sử thay đổi** | the versions that are **over** | `SUPERSEDED` and `DISCARDED` |

**An active draft is not history**, and the bug this shape exists to fix was
exactly that framing. With an approved v3 and a draft v4, the screen filtered
history on `is_current` alone — so v4 landed in the collapsed accordion, where
no control acts on it. The only visible action was *"Tạo bản điều chỉnh"* over
v3, which the service refuses because `uq_pr_work_plans_draft` allows one
revision in flight. One control that always errored, and a draft with nowhere to
be continued, approved or discarded from: clearing an empty v4 needed a database.

Both flags are decided **server-side**. `is_current` and `is_active_draft` are
different rows whenever a revision is under way, and a browser re-deriving
either would be re-implementing the partial unique index that guarantees it.

**While a draft exists, creating another revision is not a normal UI action.**
`can_revise` is false for an approved plan that already has one, so the control
is not drawn. The three acts available on the draft are:

* **Tiếp tục chỉnh sửa** — opens the existing draft. Creates no version;
* **Duyệt** — the canonical `PrWorkPlanService.approve`, which supersedes the
  plan in force *and* recomputes the period's eligibility in the same
  transaction. Never a status write from a router;
* **Bỏ bản nháp** — `DISCARDED`. Not a deletion: the row and its audit trail
  stay, so the version sequence has no hole and *"who proposed this and who
  dropped it"* stays answerable.

Creating a revision does not supersede the plan in force, editing it does not
change it, and discarding it does not touch it. **Only a successful approval
moves v3 to `SUPERSEDED`.**

### Two counts, because they answer two questions

* `history_count` — every version, the current plan and any active draft
  included. What a row prints as *"N phiên bản"*;
* `terminal_count` — the versions that are over. What *"Lịch sử thay đổi (N)"*
  shows.

They are separate fields rather than one reinterpreted, because counting a
revision somebody is writing as history is the framing that hid it.

### One creation path per state

`create_plan` starts the **first** plan for an employee-month. `revise` changes
one that is in force. They are not interchangeable, and the service refuses the
wrong one:

| State | `create_plan` | `revise` |
|---|---|---|
| nothing exists | **v1 `DRAFT`** | — (no plan to revise) |
| initial `DRAFT` | `draft_already_exists` | `plan_not_approved` |
| `APPROVED`, no draft | `approved_plan_requires_revision` | **next `DRAFT`, quotas copied** |
| `APPROVED` + `DRAFT` | `draft_already_exists` | `draft_already_exists` |

The two refusals are **different codes on purpose**, because they lead to
different recoveries: `draft_already_exists` means *continue the revision
somebody started*, `approved_plan_requires_revision` means *start one*. Both
carry the `plan_id` and `version_no` they collided with, so a screen can open
that plan instead of dead-ending. The English messages are messages, not
interfaces.

Before the guard, `create_plan` refused only a second *draft*, so it also
produced a second, semantically different revision path:

```
revise(v3)     -> v4 DRAFT, supersedes_plan_id = v3, carrying v3's quotas
create_plan()  -> v4 DRAFT, supersedes_plan_id = NULL, with none
```

The second is the `v4 · 0 hạn mức` under an approved v3 seen in production —
reaching for *Tạo kế hoạch* instead of *Tạo bản điều chỉnh*. Approving it would
have dropped five quotas nobody decided to drop.

The check is unlocked, and that is sufficient: it is the courteous half. Two
racing requests would both go on to insert a `DRAFT`, and
`uq_pr_work_plans_draft` refuses the second — the index is the rule here exactly
as it is for the draft check beside it.

### An empty draft is a state, not a fault

`revise` copies the source plan's quotas, so a revision never *starts* empty —
and `create_plan` can no longer make one beside a plan in force. A draft can
still be emptied: a manager removes the quota they mean to replace and has not
added the new one yet, which is an ordinary moment in the middle of editing.

M2 refuses to approve a plan with no quotas (`_validate_for_approval`), so
`can_approve` is false and the screen shows *Duyệt* **disabled with the reason**
rather than hidden. *Tiếp tục chỉnh sửa* and *Bỏ bản nháp* stay available: the
way out of an incomplete draft must never be to open a database.

### Losing the create-revision race

Hiding the button is a courtesy; `uq_pr_work_plans_draft` is the rule, and two
browsers can both have rendered before either clicked. The loser gets
`details.reason = "draft_already_exists"` with the `plan_id` it collided with —
a **structured code**, which is what the screen keys on. It refreshes onto the
existing draft and says so in Vietnamese; the English message is a message, not
an interface, and is never rendered.

---

## CONTENT is not RECURRING

**The load-bearing invariant**, and it is structural rather than conventional:

1. `CONTENT` work is created only by M3/M3.1's projection;
2. `RECURRING` work is created only from a template a manager explicitly
   defined;
3. `pr_work_recurring_templates` **has no `content_id` column** — a template that
   could carry one is a template somebody would eventually point at a content
   item, and that day one deliverable would exist twice;
4. the content projector creates no templates;
5. the recurring generator **reads no content table** — asserted against the
   module's source, because a scheduler that consulted content rows to decide
   what to generate would be the content workflow reimplemented on a timer.

A repeated content workflow is still **content**. Scripts written daily do not
become a recurring routine; they become daily content work.

A legitimate month may of course contain all three sources — 20 content scripts,
2 assigned research jobs, 4 recurring reports — and each enters the ledger once,
through its own source.

---

## Operational state is not KPI accounting

```
Content milestone → CONTENT work (COMPLETED) → independent validation → COUNTED
                                                                          ↓
                                                      M2 eligibility → M6 workload
```

A card saying **"Chờ xác nhận"** is M1's operational state. It says nothing about
M2. The KPI comparison counts `PrWorkContribution` rows through M2's allocation
pipeline — never rows on a screen, never recurring **templates**, never content
records directly.

Content-derived work appears **already completed**: the content workflow is the
source of truth, and asking the employee to press Start and Complete on work
they already delivered would be asking them to do it twice.

---

## Where the rules live, unchanged

| Question | Answered by |
|---|---|
| Is this valid completed work? | **M1** — `PrWorkService.approve` |
| Is it inside an approved quota? | **M2** — `PrWorkQuotaEligibilityService` |
| Which content milestone is workload? | **M3.1** — and this patch reads it, never redefines it |
| Should it exist again tomorrow? | **M4B** — the template |
| What is a month's performance? | **M6** |
| *When did it happen, and which month is it in?* | **this patch** — and that is the whole of its job |
