# M4 — Manual & Recurring Work

**Status: M4A and M4B complete and checkpointed. Nothing deployed.**

> **M4 creates and manages operational work. It counts nothing.**
> `Manual/Recurring → M1 → M2 → M6`, and every arrow is somebody else's.

> **Period containers.** [`WORK_RESULTS_BY_PERIOD.md`](WORK_RESULTS_BY_PERIOD.md)
> (migration `0039`) adds a second shape to a routine: with *Tích lũy kết quả
> theo kỳ* on, a firing ensures **one container per assignee for the month**
> and results are reported into it, rather than one job per firing with a fixed
> quantity. Everything below still describes the default shape, and the
> scheduler ledger - cursor, pause, catch-up, closed-period skip, idempotency key
> - is unchanged for both.
>
> **Navigation note.** The post-M4 UX consolidation removed the top-level
> *Định kỳ* tab. Everything in this document — the templates, the occurrence
> ledger, the generator, the cursor, the pause window, the idempotency key, the
> endpoints — is unchanged; only the way a manager reaches it moved. A routine
> is now created in *Công việc → Giao công việc* by answering *Hình thức: Định
> kỳ*, and managed from *Quản lý việc định kỳ* on the same screen. See
> [`POST_M4_UNIFIED_WORK_ASSIGNMENT.md`](POST_M4_UNIFIED_WORK_ASSIGNMENT.md).

---

## Why this milestone exists

Most of what a PR department does never touches the content workflow. Seeding,
comment work, account care, shooting, event execution, reports, research, CTV
coordination, page recovery, campaign operations, the daily and weekly routines
— none of it originates from a content item, so before M4 none of it reached the
Work Ledger.

That is not a missing feature. It is a **correctness problem for M6**: the
performance engine computes exactly from what the ledger holds, so a ledger
missing half the department's real work produces a confident, precise and
misleading performance figure. M4 closes that gap by making those jobs
first-class work.

## The core principle

**There is one Work Ledger.** M4 introduces no `ManualTask`, no `RecurringTask`,
no parallel workload system. Manual and recurring work become the *same*
`PrWorkItem` + `PrWorkContribution` rows that M1, M2 and M6 already read.

```
MANUAL                             RECURRING  (M4B)
──────                             ─────────
manager assigns / employee         active template
proposes                                 ↓
      ↓                            scheduler occurrence
PrWorkItem (source_type=MANUAL)          ↓
      ↓                            PrWorkItem (source_type=RECURRING)
ACCEPTED / IN_PROGRESS                   ↓
      ↓                            ── the same M1 lifecycle ──
COMPLETED                                ↓
      ↓
independent validation  ←── the only thing that counts, and it is M1's
      ↓
COUNTED
      ↓
M2 eligibility  →  M6 standard minutes  →  performance report
```

---

# M4A — Manual Work

## What M1 already had, and what M4A did not rebuild

The Part A audit found that **manual work already existed**. `PrWorkService`
ships two creation entry points and M4A reused both unchanged:

| M1 already provided | M4A's use of it |
|---|---|
| `propose_work` → `PROPOSED` | The employee self-proposal flow, untouched |
| `assign_work` → `ACCEPTED` | The manager assignment flow, untouched |
| `accept` refusing the proposer | The anti-gaming rule, untouched |
| `approve` refusing a contributor | The validation rule, untouched |
| `source_type` hard-coded to `MANUAL` in `_create` | The content-duplication guard, structurally |
| quantity/unit copied from the work type | The "100 comments is one item" rule |

**There was no architectural gap to report.** Part G asked us to stop and report
if M1 could not cleanly represent manager-assigned accepted work; it can, and
the asymmetry is deliberate and documented — *the assignment is the
authorization*, which is exactly why an employee cannot perform it.

So M4A required **no migration**. `0035` remains the head.

## What M4A added

### 1. `PrWorkAssignmentMode` — one job, or one job each

Two genuinely different business facts that a multi-assignee form would
otherwise collapse:

| Mode | Means | Produces |
|---|---|---|
| `SHARED_WORK` | One job several people worked on | 1 item, *n* contributions |
| `SEPARATE_PER_ASSIGNEE` | The same instruction handed to several people | *n* items, 1 contribution each |

The dangerous direction is recording the second as the first: one person then
completes the job for all three, one validation counts all three, and nobody can
be late on their own work. So the mode is **required whenever more than one
person is named** rather than defaulted. With a single assignee both modes
produce identical rows and the field means nothing.

**It is not a column.** A shared item is fully described by its three
contributions and a separate one by its single contribution; a stored mode would
be a field nothing reads and nothing keeps true. M4B stores it on the *template*,
where it genuinely is a fact that must survive until the next occurrence.

### 2. A quantity rule for quantity-measured work types

M1 left `quantity` optional for every type, which was right while nothing read
it. M2 then made `default_quota_basis` decide how counted work is *reported*, and
a `QUANTITY` type filed without a number is not a small omission — it reads as
zero comments against a plan expressed in comments. M4A refuses it at creation,
because by the time anybody notices the work may already be counted.

`ITEM_COUNT` is unchanged: one job is one unit, quantity or not.

### 3. Bulk validation

`PrWorkBulkValidationService`, modelled on the content module's
`PrBulkApprovalService` and holding the same shape:

- **not a second way to count work** — every item goes through
  `PrWorkService.approve`, the same method the single-item button calls, so the
  self-validation rule, the transition matrix, the history, the audit row, the
  notification and the M2 handoff all happen exactly once per job;
- **all-or-nothing**, structurally: one transaction, every row locked up front in
  a deterministic order (ascending by id as text), three passes — lock, screen,
  write;
- **a preflight**, because all-or-nothing is a hostile promise to debug. A
  validator who ticks forty rows and is told "4 không hợp lệ" would otherwise
  bisect. The preflight names each blocked row with the server's own sentence;
- **200 items**, the same bound as bulk approval and deliberately the same
  number rather than a second one to remember.

There is no bulk complete, no bulk cancel and no bulk reopen.

### 4. A readiness diagnostic — and why it is not a rule

A real job can be created, done, validated, counted, and still be worth nothing
on a performance report, because two independent things must be configured:

- an **approved KPI quota** for that person, that work type, that month —
  without it M2 allocates `NO_QUOTA` and M6's eligible amount is zero;
- an **approved M6 workload rule** for that work type — without it M6 reports
  `NO_SCORING_RULE` rather than inventing a rate.

`GET /api/pr/work/readiness` reports both. It **blocks nothing**: "Kháng page
David" is real work whether or not anybody has written a scoring rule for page
recovery, and a form that refused it would teach people to file real work under
whichever type happened to be configured — the exact mismeasurement the ledger
exists to prevent.

**And M4 does not fix it by writing a quota.** Creating one because somebody
assigned work would let any `PR_WORK_MANAGE` holder write KPI capacity for
anybody, defeating M2's approval gate by side effect. Whether manager-authorised
ad-hoc work should carry its own eligibility is a real product question and an
**M2 policy decision**.

### 5. A source filter

`MANUAL`, `RECURRING`, `CONTENT` — a filter and **not** a permission. It narrows
whatever the caller's scope already allows, exactly like M3.1's `content_id`, so
an employee filtering to `CONTENT` sees their own content-derived work and
nothing new. The summary tiles narrow with it, because a figure over a list that
disagrees with it is worse than no figure.

## M4A API

| Route | Method | Capability | Notes |
|---|---|---|---|
| `/api/pr/work/proposals` | POST | `PR_WORK_EXECUTE` | Unchanged from M1 |
| `/api/pr/work` | POST | `PR_WORK_MANAGE` | Unchanged from M1 |
| `/api/pr/work/batch` | POST | `PR_WORK_MANAGE` | **New.** Mode required for >1 assignee |
| `/api/pr/work/readiness` | GET | `PR_WORK_EXECUTE` | **New.** Diagnostic only |
| `/api/pr/work/validate/preflight` | POST | `PR_WORK_VALIDATE` | **New.** Writes nothing |
| `/api/pr/work/validate` | POST | `PR_WORK_VALIDATE` | **New.** All-or-nothing |
| `/api/pr/work` | GET | scope-dependent | `source_type` filter added |
| `/api/pr/work/summary` | GET | scope-dependent | `source_type` filter added |

`/readiness` is registered **before** `GET /{work_item_id}` — FastAPI matches in
declaration order, and a dynamic path declared first would take `readiness` as a
work item id.

## What M4A deliberately did not do

- no new lifecycle, no new status, no new count status;
- no write to `PrWorkQuotaAllocation` or `PrWorkScoreAllocation`;
- no performance arithmetic of any kind;
- no manual path to `CONTENT` source work;
- no fuzzy duplicate detection by title;
- no team hierarchy;
- no migration.

---

# M4B — Recurring Work

**Complete.** Migration `0036_pr_manual_recurring_work`, `down_revision = 0035`.

## The shape, in one diagram

```
ACTIVE template  ──lock──►  next_after(cursor)
                                  │
                                  ▼
                       reserve occurrence  (own commit, PENDING)
                                  │
                                  ▼
                    closed / locked month?  ──yes──►  SKIPPED_CLOSED_PERIOD
                                  │ no                (terminal, once)
                                  ▼
              PrWorkService.generate_recurring_work  ──►  PrWorkItem (ACCEPTED)
                                  │                        + contributions
                                  ▼                        ─ one transaction ─
                             GENERATED
                                  │
                                  ▼
                          cursor = occurrence
```

Everything measurable happens to the right of `PrWorkItem`, and none of it is
M4B's. A generated job is completed by its contributor, validated by somebody who
did not do it, allocated by M2 and scored by M6 — through the same methods a
hand-typed job uses.

## The three-segment source key

`SOURCE_KEY_PATTERN` is `{source}:{uuid}:{MILESTONE}` with the milestone segment
bounded to `[A-Z][A-Z0-9_]{2,39}`. The M0 document's four-segment example does
not fit it, and **nothing was widened**:

```
recurring:{occurrence-uuid}:SHARED                       # SHARED_WORK
recurring:{occurrence-uuid}:A_{32 hex, upper-cased}      # SEPARATE_PER_ASSIGNEE
```

The key has to distinguish three facts — the template, the firing, and (in
separate mode) the person — in a format with two variable segments. Naming the
**occurrence row** in the uuid segment moves the date into a row that already has
to exist, unique on `(template_id, occurrence_key)`, and leaves the third segment
free to carry the assignee.

The assignee is its **whole** id in upper-case hex, thirty-four characters with
the `A_` prefix, not a hash: a truncated id would make "one item per assignee" a
probabilistic guarantee, and the unique index over it would then silently swallow
somebody's work rather than duplicate it.

`occurrence_key` itself is the **local** wall clock — `20260904T0900`. "Báo cáo 9
giờ sáng ngày 4" is a statement about a Vietnamese clock; a key written in UTC
would name the same firing `20260904T0200` and, on a deployment that ever moved
timezone, would name two different firings the same thing.

## The cursor, and why it is not `last_generated_at`

`pr_work_recurring_templates.last_evaluated_occurrence_at` means exactly:

> every occurrence at or before this instant has a durable ledger row, or fell in
> an interval this template was deliberately not running.

Not `last_generated_at`, because an occurrence declined for a closed month, or
one that failed and is waiting to be retried, has been **evaluated** without being
**generated** — and a cursor that only moved on success would either re-walk
settled ground for ever or need a second column to remember it had.

Three methods move it, and each is a product decision:

| Act | Cursor | Why |
|---|---|---|
| `activate` | `max(local midnight of start_date, now)` | No historical flood |
| `resume` | `max(cursor, now)` | A pause is never backfilled |
| the sweep | to each occurrence it evaluates | Forward only |

**Editing does not move it.** An edit is not a statement about which occurrences
have been dealt with.

## The occurrence states

`PENDING` → `GENERATED`, `SKIPPED_CLOSED_PERIOD` or `FAILED_RETRYABLE`. Four, and
each makes a different failure impossible:

* **`PENDING`** is committed *before* any work exists, so a worker that dies
  mid-generation leaves a row saying the work was owed rather than nothing;
* **`GENERATED`** is written in the *same* transaction as the work items, so
  there is no instant at which the ledger claims work that is not there. A CHECK
  pairs it with `generated_at`, and a second refuses `work_item_count > 0` on
  anything else;
* **`SKIPPED_CLOSED_PERIOD`** is terminal. The reason will be true for ever, so
  retrying would loop for ever;
* **`FAILED_RETRYABLE`** is the retry set, which the sweep drains *before* it
  walks forward.

**There is no state for "skipped because paused"** and none for "not yet
evaluated". A pause is an interval the sweeper never walks — resuming moves the
cursor past it, so it produces no rows at all, which is also why a template paused
for a year does not owe the database three hundred rows saying nothing happened.
"Not yet evaluated" is the absence of a row, bounded by the cursor.

## Pause versus downtime

The distinction the whole design turns on, and it is a distinction in **who moved
the cursor**:

| | Who acted | Cursor | Result |
|---|---|---|---|
| **Pause** | a manager decided | jumps to the resume instant | the interval produces nothing, ever |
| **Downtime** | nobody | stays where it was | the missed firings are generated on the next sweep |

Modelling a pause as downtime hands a manager who suspended a routine over Tết a
fortnight of backdated work the moment they turn it back on. Modelling downtime as
a pause silently loses work the department genuinely owed.

Catch-up is bounded twice, and the two bounds answer different questions:
`MAX_OCCURRENCES_PER_SWEEP` limits a **batch**, `MAX_CATCH_UP_DAYS` limits
**history**.

## Reporting-period safety

An occurrence whose scheduled instant falls in a `CLOSED` or `LOCKED` month is
settled as `SKIPPED_CLOSED_PERIOD`, once, with the period named on the row —
because *"the routine produced nothing in the last week of August"* is a question
somebody asks in September, and it should be answerable without a log aggregator.

An `OPEN` period generates. **A month with no period row also generates**:
`period_for` returning `None` is M2's deliberate answer that nobody has set the
month up — an operational fact, not a lock — and refusing work because of it would
let a missing configuration row silently delete the department's work.

## The operational quantity boundary

M4A put the `QUANTITY` rule in the **router**, on the reasoning that every way a
person files work is an HTTP call. M4B made that reasoning false: the generator
files work from a beat sweep and touches no router. The rule moved down **exactly
one level**, and the level it stopped at is the point:

| Layer | Rule applies? | Because |
|---|---|---|
| `propose_work`, `assign_work`, `assign_work_batch`, `generate_recurring_work`, template create/update/activate | **yes** | somebody is *commanding work into existence* |
| `_create`, `create_source_work`, the row itself | **no** | *representation* — M2's `MISSING_QUANTITY` describes something that can still be true of rows already in the table |

`PrWorkService._validate_operational_command` is the seam. A service-level caller
who never touched FastAPI now meets the rule; M2's diagnostic state is still
constructible, still stored, and still measured as `MISSING_QUANTITY`.

What is gone is the ability to *ask* for that state through a command — which was
never its reason for existing.

## Template revisions

`revision_no` is bumped by every edit and stamped onto each occurrence. The
generator locks the template row while it builds one, so a sweep that started
before an edit and finished after it produced work entirely from one revision, and
the occurrence says which. **Work already generated is never rewritten**: a job
created last Tuesday records what was asked for last Tuesday.

## Standing authorization

An `ACTIVE` template is a manager saying *"this happens every day, and I am asking
for it"*. So:

* `activated_by_user_id` is the actor of every generated job — `created_by`,
  `assigned_by`, and the audit row's actor. Never a worker process;
* the work lands at `ACCEPTED` — *the assignment is the authorization*, exactly as
  for `assign_work`;
* it is **not** `COMPLETED`, `APPROVED` or `COUNTED`. Activation is not doing the
  work and not validating it, and M1's independent approval is still the only
  thing that can make any of it count;
* if that account is gone or deactivated, generation **fails** rather than
  proceeding on an authority that no longer exists.

## Concurrency

Two unique indexes, and no claim column:

* `uq_template_occurrence` over `(template_id, occurrence_key)` — two beat workers
  reserving the same firing produce one row;
* `uq_pr_work_items_source` over `(source_type, source_key)` — a retry after a
  crash composes the identical key and collides.

There is deliberately **no claim**, unlike `pr_channel_sync`: a claim is a lock
somebody has to remember to release, and the recovery task this module does not
need is the one that module does.

## M4B API

| Route | Method | Capability | Notes |
|---|---|---|---|
| `/api/pr/work/recurring` | GET | `PR_WORK_MANAGE` | Optional `status` filter |
| `/api/pr/work/recurring` | POST | `PR_WORK_MANAGE` | Lands at `DRAFT` |
| `/api/pr/work/recurring/preview` | POST | `PR_WORK_MANAGE` | **Writes nothing** |
| `/api/pr/work/recurring/{id}` | GET / PUT / DELETE | `PR_WORK_MANAGE` | Delete: unused draft only |
| `/api/pr/work/recurring/{id}/occurrences` | GET | `PR_WORK_MANAGE` | The scheduler's history |
| `/api/pr/work/recurring/{id}/activate` | POST | `PR_WORK_MANAGE` | The authorization |
| `/api/pr/work/recurring/{id}/pause` | POST | `PR_WORK_MANAGE` | |
| `/api/pr/work/recurring/{id}/resume` | POST | `PR_WORK_MANAGE` | No backfill |
| `/api/pr/work/recurring/{id}/end` | POST | `PR_WORK_MANAGE` | Terminal |

Mounted **before** M1's work router — it owns the literal `/api/pr/work/recurring`
prefix and M1 owns `GET /api/pr/work/{work_item_id}`, and FastAPI matches in
registration order. The same ordering M2's `/periods` and M3's `/content` needed.

`PR_WORK_MANAGE`, not `PR_WORK_CONFIGURE`: a recurring template *is* an
assignment, repeated, and a Trưởng nhóm who may assign work may say "every day".
Owning the **taxonomy** is the different act and keeps `PR_WORK_CONFIGURE`.

## Scheduling

`pr.sweep_recurring_work` on beat every five minutes dispatches
`pr.generate_recurring_work` per active template. `pr_recurring_work_enabled` is a
kill switch: templates keep their state and their cursors, and catch-up resumes
from where it stopped when it is turned back on.

Unlike every other PR task, the worker uses `database.session()` rather than
`database.transaction()` — the generator's correctness *is* a sequence of commits,
and one enclosing transaction would collapse them and the crash-recovery guarantee
with them.

## What M4B deliberately did not do

- **no "run now" route and no backfill route.** Generation happens on the sweep,
  under the rules that make it safe; an endpoint that produced work on demand
  would be a second implementation of those rules and the one people reached for
  when the first said no;
- no cron, no RRULE, no second recurrence language — `RecurringSchedule` composes
  `ReminderSchedule` for daily and weekly, and adds monthly;
- no change to `ReminderSchedule` itself: widening its `WEEKLY` to several
  weekdays would change the meaning of `recurrence_rule` strings already stored
  against live reminders;
- no widening of `SOURCE_KEY_PATTERN`;
- no new lifecycle, no new work status, no new count status;
- no write to `PrWorkQuotaAllocation` or `PrWorkScoreAllocation`, and no
  arithmetic of any kind;
- no cron string, cursor, source key or occurrence internal on any request body,
  and none in the editor UI.

## Where the rules actually live

| Question | Answered by |
|---|---|
| Is this valid completed work? | **M1** — `PrWorkService.approve` |
| Is it inside an approved quota? | **M2** — `PrWorkQuotaEligibilityService` |
| What is a kind of work worth? | **M6** — the approved scoring rule |
| What is this month's performance? | **M6** — `PrPerformanceService` |
| Does this work exist at all? | **M4** — and that is the whole of its job |
| Should it exist again tomorrow? | **M4B** — the template, and nothing else it decides |
